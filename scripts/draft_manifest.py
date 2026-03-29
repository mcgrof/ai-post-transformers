"""Build and update admin manifest draft entries from local DB metadata.

The admin Drafts UI reads podcast-admin/manifest.json for title,
description, and source information.  When drafts are generated via
the submission path, the generation worker must push a manifest entry
so the admin UI shows rich metadata instead of just a playable MP3.

This module provides:
- build_manifest_entry(): construct one manifest draft entry from a
  podcasts DB row.
- upsert_manifest_draft(): read-modify-write the R2 manifest to add
  or update a single draft entry.
- backfill_manifest(): CLI-callable function that scans local DB for
  draft episodes missing from the manifest and adds them.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_manifest_entry(row, *, draft_key=None, draft_stem=None):
    """Build a manifest draft entry dict from a podcasts DB row.

    Args:
        row: A dict-like row from the podcasts table (must have id,
             title, publish_date, audio_file, description, source_urls,
             image_file).
        draft_key: Explicit R2 key for the draft MP3. If not provided,
                   derived from audio_file.
        draft_stem: The draft stem (without extension). If not provided,
                    derived from audio_file.

    Returns:
        Dict suitable for appending to manifest["drafts"].
    """
    audio_file = row.get("audio_file") or ""
    if not draft_stem:
        stem_path = Path(audio_file)
        draft_stem = str(stem_path.with_suffix(""))
        # Normalise to relative path from project root
        try:
            draft_stem = str(
                Path(draft_stem).relative_to(ROOT)
            ).replace(os.sep, "/")
        except ValueError:
            pass

    if not draft_key:
        draft_key = f"{draft_stem}.mp3"

    filename = Path(draft_key).name
    basename = filename.rsplit(".", 1)[0] if "." in filename else filename

    source_urls = row.get("source_urls")
    if isinstance(source_urls, str):
        try:
            source_urls = json.loads(source_urls)
        except (json.JSONDecodeError, TypeError):
            source_urls = []
    source_urls = source_urls or []

    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "date": row.get("publish_date") or "",
        "description": row.get("description") or "",
        "draft_key": draft_key,
        "filename": filename,
        "basename": basename,
        "source_urls": source_urls,
    }


def upsert_manifest_draft(entry, *, client=None, admin_bucket=None):
    """Read-modify-write the R2 admin manifest to add/update a draft.

    Matching is by entry["id"].  If an entry with the same id already
    exists it is replaced; otherwise the new entry is appended.

    Args:
        entry: Dict from build_manifest_entry().
        client: boto3 S3 client (created via get_r2_client if None).
        admin_bucket: Bucket name (defaults to ADMIN_BUCKET_NAME env).

    Returns:
        The updated manifest dict.
    """
    if client is None:
        from r2_upload import get_r2_client
        client = get_r2_client()
    bucket = admin_bucket or os.environ.get(
        "ADMIN_BUCKET_NAME", "podcast-admin"
    )

    try:
        obj = client.get_object(Bucket=bucket, Key="manifest.json")
        manifest = json.loads(obj["Body"].read())
    except Exception:
        manifest = {"drafts": [], "conferences": {}}

    drafts = manifest.get("drafts", [])
    entry_id = entry["id"]

    # Replace existing entry with same id, or append
    replaced = False
    for i, d in enumerate(drafts):
        if d.get("id") == entry_id:
            drafts[i] = entry
            replaced = True
            break
    if not replaced:
        drafts.append(entry)

    manifest["drafts"] = drafts

    client.put_object(
        Bucket=bucket,
        Key="manifest.json",
        Body=json.dumps(manifest, indent=2) + "\n",
        ContentType="application/json",
    )
    return manifest


def enrich_sidecar_json(sidecar_path, *, title=None, description=None,
                        source_urls=None, episode_id=None):
    """Add title/description/episode_id to an existing sidecar JSON.

    The sidecar JSON created by podcast.py contains script, sources,
    and topics but omits title and description.  This function patches
    those fields in so the admin worker can use the sidecar as a
    fallback when the manifest entry is missing.

    Safe to call multiple times — existing fields are overwritten only
    when new values are provided.
    """
    path = Path(sidecar_path)
    if not path.exists():
        return False

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    changed = False
    if title is not None and data.get("title") != title:
        data["title"] = title
        changed = True
    if description is not None and data.get("description") != description:
        data["description"] = description
        changed = True
    if source_urls is not None and data.get("source_urls") != source_urls:
        data["source_urls"] = source_urls
        changed = True
    if episode_id is not None and data.get("episode_id") != episode_id:
        data["episode_id"] = episode_id
        changed = True

    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n")
    return changed


def _get_draft_episodes(conn):
    """Return podcast rows whose audio_file points to a drafts/ path."""
    rows = conn.execute(
        "SELECT * FROM podcasts WHERE audio_file LIKE '%drafts/%' "
        "ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def backfill_manifest(db_path=None, *, dry_run=False, client=None,
                      admin_bucket=None):
    """Scan DB for draft episodes and ensure each has a manifest entry.

    Also enriches local sidecar JSONs with title/description.

    Args:
        db_path: Path to papers.db (uses default if None).
        dry_run: If True, print what would be done without writing.
        client: boto3 S3 client (for testing; created if None).
        admin_bucket: Bucket name override.

    Returns:
        List of (episode_id, title, action) tuples describing what
        was done.
    """
    from db import get_connection, init_db

    conn = get_connection(db_path)
    init_db(conn)
    episodes = _get_draft_episodes(conn)
    conn.close()

    if not episodes:
        print("[backfill] No draft episodes found in DB")
        return []

    # Read current manifest to check what's already there
    if not dry_run:
        if client is None:
            from r2_upload import get_r2_client
            client = get_r2_client()
        bucket = admin_bucket or os.environ.get(
            "ADMIN_BUCKET_NAME", "podcast-admin"
        )
        try:
            obj = client.get_object(Bucket=bucket, Key="manifest.json")
            manifest = json.loads(obj["Body"].read())
        except Exception:
            manifest = {"drafts": [], "conferences": {}}
    else:
        manifest = {"drafts": []}

    existing_ids = {d.get("id") for d in manifest.get("drafts", [])}

    actions = []
    for ep in episodes:
        ep_id = ep["id"]
        title = ep.get("title", "(untitled)")
        entry = build_manifest_entry(ep)

        if ep_id in existing_ids:
            actions.append((ep_id, title, "already_in_manifest"))
            continue

        # Enrich local sidecar JSON
        audio_file = ep.get("audio_file", "")
        if audio_file:
            sidecar = Path(audio_file).with_suffix(".json")
            if not sidecar.is_absolute():
                sidecar = ROOT / sidecar
            source_urls = ep.get("source_urls")
            if isinstance(source_urls, str):
                try:
                    source_urls = json.loads(source_urls)
                except (json.JSONDecodeError, TypeError):
                    source_urls = []
            enriched = enrich_sidecar_json(
                sidecar,
                title=title,
                description=ep.get("description"),
                source_urls=source_urls,
                episode_id=ep_id,
            )
            if enriched and not dry_run:
                print(f"[backfill] Enriched sidecar: {sidecar}")

        if dry_run:
            print(
                f"[backfill] Would add ep {ep_id}: {title} "
                f"(draft_key={entry['draft_key']})"
            )
            actions.append((ep_id, title, "would_add"))
        else:
            upsert_manifest_draft(entry, client=client,
                                  admin_bucket=admin_bucket)
            print(f"[backfill] Added ep {ep_id}: {title}")
            actions.append((ep_id, title, "added"))

    return actions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill admin manifest draft entries from "
                    "local papers.db metadata."
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to papers.db (uses default if omitted)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without writing to R2",
    )
    args = parser.parse_args()

    actions = backfill_manifest(db_path=args.db, dry_run=args.dry_run)

    added = sum(1 for _, _, a in actions if a == "added")
    skipped = sum(1 for _, _, a in actions if a == "already_in_manifest")
    would = sum(1 for _, _, a in actions if a == "would_add")

    if args.dry_run:
        print(f"\n[backfill] Dry run: {would} to add, {skipped} already present")
    else:
        print(f"\n[backfill] Done: {added} added, {skipped} already present")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
