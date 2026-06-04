#!/usr/bin/env python3
"""Clean up stale and orphaned submission records from SQLite and R2.

Removes submissions that:
- Have draft_generated/approved_for_publish status but no corresponding MP3
- Are older than 30 days and still in draft status
- Have draft_stem pointing to a published episode (in episodes/)

This script uses file locking to prevent concurrent execution with the
podcast worker. It's safe to run on a timer (e.g., daily via systemd).

Usage:
    # Dry run (default - shows what would be deleted)
    python scripts/cleanup_stale_submissions.py

    # Actually delete (requires --execute flag)
    python scripts/cleanup_stale_submissions.py --execute

    # Skip if another worker is running (for cron/systemd)
    python scripts/cleanup_stale_submissions.py --execute --skip-if-locked

    # Adjust cutoff
    python scripts/cleanup_stale_submissions.py --execute --old-days 60
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _acquire_lock(skip_if_locked=False, timeout=1):
    """Acquire exclusive lock to prevent concurrent execution.

    Args:
        skip_if_locked: If True, return None instead of blocking if lock
                       can't be acquired immediately.
        timeout: Seconds to wait for lock before giving up.

    Returns:
        Lock file handle if acquired, None if skip_if_locked=True and
        lock couldn't be acquired.
    """
    import fcntl
    import atexit

    lock_path = Path.home() / ".run/submission-cleanup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = open(lock_path, "w")

    try:
        if skip_if_locked:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        atexit.register(lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN))
        return lock_file
    except IOError:
        if skip_if_locked:
            lock_file.close()
            return None
        print("error: cleanup already running (locked by another process)")
        sys.exit(1)


def cleanup_submissions(*, dry_run=True, old_days=30, skip_if_locked=False):
    """Remove stale submissions from both SQLite and R2."""
    import sqlite3
    from pathlib import Path
    from r2_upload import get_r2_client

    # Acquire lock if needed
    if skip_if_locked:
        lock = _acquire_lock(skip_if_locked=True)
        if lock is None:
            print("Cleanup already running, skipping")
            return
    else:
        lock = _acquire_lock(skip_if_locked=False)

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
        "--execute", action="store_true",
        help="Actually delete stale submissions (default: dry-run only)"
    )
    parser.add_argument(
        "--skip-if-locked", action="store_true",
        help="Exit silently if cleanup is already running (for systemd timer)"
    )
    parser.add_argument(
        "--old-days", type=int, default=30,
        help="Remove pending submissions older than N days (default: 30)"
    )
    args = parser.parse_args()

    cleanup_submissions(
        dry_run=not args.execute,
        old_days=args.old_days,
        skip_if_locked=args.skip_if_locked
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
