#!/usr/bin/env python3
"""Clean up draft metadata and manifest to prevent published episodes from
appearing as drafts.

This script:
1. Fixes episodes with audio_file in public/ but not marked as published
2. Removes orphaned episodes with missing audio files
3. Cleans manifest.json to remove published episodes
4. Ensures all draft entries have valid metadata
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
from r2_upload import get_r2_client


def get_public_episodes(conn):
    """Find unpublished episodes with audio in public/ directory."""
    rows = conn.execute(
        "SELECT id, audio_file FROM podcasts "
        "WHERE published_at IS NULL AND audio_file LIKE '%public/%'"
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def get_orphaned_episodes(conn):
    """Find unpublished episodes with missing audio files."""
    rows = conn.execute(
        "SELECT id, audio_file FROM podcasts "
        "WHERE published_at IS NULL AND audio_file IS NOT NULL"
    ).fetchall()
    
    orphaned = []
    for ep_id, audio_file in rows:
        if not Path(audio_file).exists():
            orphaned.append(ep_id)
    return orphaned


def fix_database(dry_run=False):
    """Fix database inconsistencies."""
    conn = get_connection()
    init_db(conn)
    
    # Fix unpublished episodes with public/ audio
    public_eps = get_public_episodes(conn)
    if public_eps:
        print(f"Found {len(public_eps)} episodes in public/ but unpublished:")
        for ep_id, audio_file in public_eps:
            # Extract date from filename
            basename = Path(audio_file).name
            if basename.startswith("2026-"):
                date_part = basename[:10]  # YYYY-MM-DD
            else:
                date_part = datetime.now().date().isoformat()
            
            print(f"  Marking EP {ep_id} as published ({date_part})")
            if not dry_run:
                conn.execute(
                    "UPDATE podcasts SET published_at = ? WHERE id = ?",
                    (f"{date_part}T00:00:00", ep_id)
                )
        conn.commit()
    
    # Clear audio_file for orphaned episodes
    orphaned = get_orphaned_episodes(conn)
    if orphaned:
        print(f"Found {len(orphaned)} orphaned episodes with missing audio:")
        for ep_id in orphaned:
            print(f"  Clearing audio_file for EP {ep_id}")
            if not dry_run:
                conn.execute(
                    "UPDATE podcasts SET audio_file = NULL WHERE id = ?",
                    (ep_id,)
                )
        conn.commit()
    
    conn.close()


def clean_manifest(dry_run=False):
    """Remove published episodes and corrupted entries from manifest."""
    client = get_r2_client()
    bucket = "podcast-admin"
    
    # Get manifest
    try:
        obj = client.get_object(Bucket=bucket, Key="manifest.json")
        manifest = json.loads(obj["Body"].read())
    except Exception as e:
        print(f"Warning: Could not read manifest: {e}")
        return
    
    # Get published IDs from DB
    conn = get_connection()
    init_db(conn)
    published_ids = {
        row[0] for row in conn.execute(
            "SELECT id FROM podcasts WHERE published_at IS NOT NULL"
        ).fetchall()
    }
    conn.close()
    
    original_count = len(manifest.get("drafts", []))
    
    # Filter out published and corrupted entries
    cleaned_drafts = [
        d for d in manifest.get("drafts", [])
        if d.get("id") is not None and d.get("id") not in published_ids
    ]
    
    removed_count = original_count - len(cleaned_drafts)
    
    if removed_count > 0:
        manifest["drafts"] = cleaned_drafts
        if not dry_run:
            client.put_object(
                Bucket=bucket,
                Key="manifest.json",
                Body=json.dumps(manifest, indent=2) + "\n",
                ContentType="application/json",
            )
        print(f"Cleaned manifest: removed {removed_count} published/invalid entries")
        print(f"  Remaining valid drafts: {len(cleaned_drafts)}")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Clean draft metadata and prevent published episodes "
                    "from showing in draft list"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fixed without making changes"
    )
    args = parser.parse_args()
    
    print("[clean-draft-metadata] Fixing database inconsistencies...")
    fix_database(dry_run=args.dry_run)
    
    print("[clean-draft-metadata] Cleaning manifest.json...")
    clean_manifest(dry_run=args.dry_run)
    
    print("[clean-draft-metadata] Done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
