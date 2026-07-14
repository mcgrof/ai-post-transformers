#!/usr/bin/env python3
"""Fix missing/stale manifest entries and sidecar JSON metadata for episodes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
from scripts.draft_manifest import build_manifest_entry, upsert_manifest_draft, enrich_sidecar_json
from r2_upload import get_r2_client


def fix_episode_metadata(episode_id: int) -> bool:
    """Rebuild manifest entry and sidecar JSON for a given episode.

    Args:
        episode_id: Database ID of the podcast episode.

    Returns:
        True if successful, False otherwise.
    """
    conn = get_connection()
    init_db(conn)

    # Fetch episode from database
    row = conn.execute(
        "SELECT * FROM podcasts WHERE id = ?", (episode_id,)
    ).fetchone()

    if not row:
        print(f"Episode {episode_id} not found in database")
        conn.close()
        return False

    ep = dict(row)
    print(f"Found episode {episode_id}: {ep.get('title', '?')}")
    print(f"  Audio file: {ep.get('audio_file', '?')}")
    print(f"  Description: {bool(ep.get('description'))}")

    # Rebuild the draft_key and draft_stem from audio_file
    audio_file = ep.get("audio_file", "")
    if not audio_file:
        print(f"  Error: audio_file is empty")
        conn.close()
        return False

    # Extract the stem from the audio file path
    # e.g., "drafts/2026/07/2026-07-14-a-catalog-driven-framework-for-natural-l-7ba353.mp3"
    # becomes "2026-07-14-a-catalog-driven-framework-for-natural-l-7ba353"
    audio_path = Path(audio_file)
    draft_stem = audio_path.stem  # removes .mp3 extension
    draft_key = f"{draft_stem}.mp3"

    print(f"  Draft stem: {draft_stem}")
    print(f"  Draft key: {draft_key}")

    # Build and upsert manifest entry
    entry = build_manifest_entry(
        ep, draft_key=draft_key, draft_stem=draft_stem
    )
    print(f"\nManifest entry fields:")
    print(f"  title: {entry.get('title')}")
    print(f"  description: {bool(entry.get('description'))}")

    upsert_manifest_draft(entry)
    print("✓ Manifest entry upserted")

    # Enrich sidecar JSON with title and description
    sidecar_path = audio_path.with_suffix(".json")
    print(f"\nSidecar JSON: {sidecar_path}")

    # For local testing, check if file exists locally
    local_sidecar = ROOT / sidecar_path
    if local_sidecar.exists():
        print(f"  Found locally: {local_sidecar}")
        result = enrich_sidecar_json(
            local_sidecar,
            title=ep.get("title"),
            description=ep.get("description"),
            episode_id=ep["id"],
        )
        if result:
            print(f"  ✓ Local sidecar updated")
        else:
            print(f"  (no changes needed)")
    else:
        print(f"  Not found locally - R2-only draft")
        # Try to update R2 sidecar
        try:
            client = get_r2_client()
            # Download sidecar from R2
            r2_key = f"{draft_stem}.json"
            resp = client.get_object(Bucket="ai-post-transformers", Key=r2_key)
            sidecar_data = json.loads(resp["Body"].read().decode())

            changed = False
            if sidecar_data.get("title") != ep.get("title"):
                sidecar_data["title"] = ep.get("title")
                changed = True
            if sidecar_data.get("description") != ep.get("description"):
                sidecar_data["description"] = ep.get("description")
                changed = True
            if sidecar_data.get("episode_id") != ep["id"]:
                sidecar_data["episode_id"] = ep["id"]
                changed = True

            if changed:
                # Upload updated sidecar
                sidecar_json = json.dumps(sidecar_data, indent=2) + "\n"
                client.put_object(
                    Bucket="ai-post-transformers",
                    Key=r2_key,
                    Body=sidecar_json.encode(),
                    ContentType="application/json",
                )
                print(f"  ✓ R2 sidecar updated")
            else:
                print(f"  (no changes needed)")
        except Exception as e:
            print(f"  Error updating R2 sidecar: {e}")

    conn.close()
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "episode_id", type=int, help="Database ID of episode to fix"
    )
    args = parser.parse_args()

    success = fix_episode_metadata(args.episode_id)
    sys.exit(0 if success else 1)
