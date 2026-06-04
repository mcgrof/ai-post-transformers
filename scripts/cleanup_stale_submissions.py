#!/usr/bin/env python3
"""Clean up stale and orphaned submission records from SQLite and R2.

Removes submissions that:
- Have draft_generated/approved_for_publish status but no corresponding MP3
- Are older than 30 days and still in draft status
- Have draft_stem pointing to a published episode (in episodes/)

Usage:
    python scripts/cleanup_stale_submissions.py [--dry-run] [--old-days 30]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def cleanup_submissions(*, dry_run=False, old_days=30):
    """Remove stale submissions from both SQLite and R2."""
    import sqlite3
    from pathlib import Path
    from r2_upload import get_r2_client

    # Connect to queue.db where submissions live
    queue_db = Path.home() / '.local/state/ai-post-transformers/queue.db'
    if not queue_db.exists():
        print(f"queue.db not found at {queue_db}")
        return

    conn = sqlite3.connect(queue_db)
    conn.row_factory = sqlite3.Row
    client = get_r2_client()

    # Get list of published episode basenames from episodes/
    published_basenames = set()
    try:
        list_resp = client.list_objects_v2(
            Bucket='ai-post-transformers',
            Prefix='episodes/',
            MaxKeys=2000
        )
        for obj in (list_resp.get('Contents') or []):
            if obj['Key'].endswith('.mp3'):
                basename = obj['Key'].split('/')[-1].replace('.mp3', '')
                published_basenames.add(basename)
    except Exception as e:
        print(f"Warning: Failed to list published episodes: {e}")

    # Get list of draft MP3 files
    draft_mp3_basenames = set()
    try:
        list_resp = client.list_objects_v2(
            Bucket='ai-post-transformers',
            Prefix='drafts/',
            MaxKeys=1000
        )
        for obj in (list_resp.get('Contents') or []):
            if obj['Key'].endswith('.mp3'):
                basename = obj['Key'].split('/')[-1].replace('.mp3', '')
                draft_mp3_basenames.add(basename)
    except Exception as e:
        print(f"Warning: Failed to list draft files: {e}")

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=old_days)).isoformat()

    # Clean SQLite
    print("Cleaning SQLite submissions...")
    stale_rows = conn.execute("""
        SELECT key, status, data_json, updated_at
        FROM submissions
        WHERE status IN ('draft_generated', 'approved_for_publish')
           OR (status IN ('submitted', 'generation_claimed', 'generation_running')
               AND updated_at < ?)
    """, (cutoff_date,)).fetchall()

    deleted_from_sqlite = 0
    for row in stale_rows:
        key = row[0]
        status = row[1]
        data = json.loads(row[2])
        draft_stem = data.get('draft_stem', '')
        basename = draft_stem.split('/')[-1] if draft_stem else ''

        # Remove if:
        # 1. No corresponding draft MP3
        # 2. Draft is published (in episodes/)
        should_delete = (
            not basename or
            basename not in draft_mp3_basenames or
            basename in published_basenames
        )

        if should_delete:
            if not dry_run:
                conn.execute("DELETE FROM submissions WHERE key = ?", (key,))
            deleted_from_sqlite += 1
            print(f"  DELETE: {key} ({status}, stem={basename})")

    if not dry_run and deleted_from_sqlite > 0:
        conn.commit()
        print(f"  Deleted {deleted_from_sqlite} from SQLite")
    elif dry_run:
        print(f"  Would delete {deleted_from_sqlite} from SQLite")

    conn.close()

    # Clean R2
    print("\nCleaning R2 submissions...")
    subs_list = client.list_objects_v2(
        Bucket='podcast-admin',
        Prefix='submissions/',
        MaxKeys=500
    )

    deleted_from_r2 = 0
    for obj in (subs_list.get('Contents') or []):
        key = obj['Key']
        try:
            data = client.get_object(Bucket='podcast-admin', Key=key)
            sub = json.loads(data['Body'].read())
            status = sub.get('status')
            draft_stem = sub.get('draft_stem', '')
            basename = draft_stem.split('/')[-1] if draft_stem else ''
            timestamp = sub.get('timestamp', '')

            should_delete = False
            reason = ""

            if status in ('draft_generated', 'approved_for_publish'):
                if not basename or basename not in draft_mp3_basenames:
                    should_delete = True
                    reason = "no MP3 file"

            if basename in published_basenames:
                should_delete = True
                reason = "published episode"

            if timestamp < cutoff_date and status in (
                'submitted', 'generation_claimed', 'generation_running'
            ):
                should_delete = True
                reason = f"stale ({status})"

            if should_delete:
                if not dry_run:
                    client.delete_object(Bucket='podcast-admin', Key=key)
                deleted_from_r2 += 1
                print(f"  DELETE: {key} ({reason})")

        except Exception as e:
            print(f"  Error processing {key}: {e}")

    if not dry_run:
        print(f"  Deleted {deleted_from_r2} from R2")
    elif dry_run:
        print(f"  Would delete {deleted_from_r2} from R2")

    print(f"\nTotal: {deleted_from_sqlite + deleted_from_r2} stale submissions removed")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up stale submission records from SQLite and R2"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    parser.add_argument(
        "--old-days", type=int, default=30,
        help="Remove pending submissions older than N days (default: 30)"
    )
    args = parser.parse_args()

    cleanup_submissions(dry_run=args.dry_run, old_days=args.old_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
