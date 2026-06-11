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


_ACTIVE_PUBLISH_STATES = {
    "approved_for_publish", "publish_claimed", "publish_running",
}
# Only these draft statuses may be advanced to 'published'.
_ADVANCEABLE_STATUSES = {"draft_generated", "approved_for_publish"}
# Pending statuses that can be deleted once old (a never-finished pickup).
_STALE_PENDING_STATUSES = {
    "submitted", "generation_claimed", "generation_running",
}


def _classify_submission(
    *, status, basename, stem, updated_at, job_states,
    draft_mp3_basenames, published_basenames, cutoff_date,
):
    """Decide what to do with a submission.

    Returns one of:
      - 'skip'    : leave it alone — terminal status (rejected/published/
                    generation_failed), mid-publish, a live draft, or too
                    recent to be a confident orphan.
      - 'advance' : a draft whose episode is already published — advance
                    the record to 'published' (never delete a published
                    episode's submission; deletion loses the record and
                    can race a publish).
      - 'delete'  : a true orphan — no publish job, no episode, no draft
                    MP3, and older than the grace window.

    This replaces the old "MP3 not in drafts/ -> delete" rule, which
    deleted episodes whose MP3 was mid-move drafts/ -> episodes/, and
    which could not see that rejected/published records must be left
    alone.
    """
    # Never touch a terminal record (rejected, published, generation_failed)
    # — only live draft statuses or old never-finished pickups are candidates.
    if status not in _ADVANCEABLE_STATUSES and status not in _STALE_PENDING_STATUSES:
        return "skip"
    if job_states & _ACTIVE_PUBLISH_STATES:
        return "skip"
    if status in _ADVANCEABLE_STATUSES and (
        "publish_completed" in job_states
        or (basename and basename in published_basenames)
    ):
        return "advance"
    if basename and basename in draft_mp3_basenames:
        return "skip"
    if (updated_at or "") < cutoff_date:
        return "delete"
    return "skip"


def cleanup_submissions(*, dry_run=True, old_days=30, skip_if_locked=False):
    """Remove stale submissions from both SQLite and R2."""
    import sqlite3
    from pathlib import Path
    from r2_upload import get_r2_client
    from scripts.publish_job_runner import _normalize_draft_stem

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
    now_iso = datetime.now(timezone.utc).isoformat()

    # Index publish-job states by normalized draft stem. This is what lets
    # us protect a mid-publish episode (its MP3 moves drafts/ -> episodes/)
    # and advance already-published records instead of deleting them.
    job_states_by_stem: dict[str, set] = {}
    for jrow in conn.execute("SELECT data_json, state FROM publish_jobs"):
        try:
            jd = json.loads(jrow[0])
        except Exception:
            continue
        jstem = _normalize_draft_stem(jd.get("draft_key", ""))
        if jstem:
            job_states_by_stem.setdefault(jstem, set()).add(jrow[1])

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
    advanced_in_sqlite = 0
    for row in stale_rows:
        key = row[0]
        status = row[1]
        data = json.loads(row[2])
        stem = _normalize_draft_stem(data.get('draft_stem', ''))
        basename = stem.split('/')[-1] if stem else ''
        action = _classify_submission(
            status=status, basename=basename, stem=stem, updated_at=row[3],
            job_states=job_states_by_stem.get(stem, set()),
            draft_mp3_basenames=draft_mp3_basenames,
            published_basenames=published_basenames, cutoff_date=cutoff_date,
        )
        if action == 'advance' and status != 'published':
            if not dry_run:
                data['status'] = 'published'
                data['updated_at'] = now_iso
                hist = data.get('status_history') or []
                hist.append({'status': 'published', 'at': now_iso})
                data['status_history'] = hist
                conn.execute(
                    "UPDATE submissions SET data_json = ?, version = version + 1, "
                    "status = ?, updated_at = ? WHERE key = ?",
                    (json.dumps(data, sort_keys=True), 'published', now_iso, key),
                )
            advanced_in_sqlite += 1
            print(f"  ADVANCE->published: {key} ({status}, stem={basename})")
        elif action == 'delete':
            if not dry_run:
                conn.execute("DELETE FROM submissions WHERE key = ?", (key,))
            deleted_from_sqlite += 1
            print(f"  DELETE: {key} ({status}, stem={basename})")

    if not dry_run:
        conn.commit()
    verb = "Deleted" if not dry_run else "Would delete"
    print(f"  {verb} {deleted_from_sqlite} from SQLite; "
          f"advanced {advanced_in_sqlite} to published")

    conn.close()

    # Clean R2
    print("\nCleaning R2 submissions...")
    subs_list = client.list_objects_v2(
        Bucket='podcast-admin',
        Prefix='submissions/',
        MaxKeys=500
    )

    deleted_from_r2 = 0
    advanced_in_r2 = 0
    for obj in (subs_list.get('Contents') or []):
        key = obj['Key']
        try:
            data = client.get_object(Bucket='podcast-admin', Key=key)
            sub = json.loads(data['Body'].read())
            status = sub.get('status')
            stem = _normalize_draft_stem(sub.get('draft_stem', ''))
            basename = stem.split('/')[-1] if stem else ''
            updated_at = sub.get('updated_at') or sub.get('timestamp', '')
            action = _classify_submission(
                status=status, basename=basename, stem=stem,
                updated_at=updated_at,
                job_states=job_states_by_stem.get(stem, set()),
                draft_mp3_basenames=draft_mp3_basenames,
                published_basenames=published_basenames,
                cutoff_date=cutoff_date,
            )
            if action == 'advance' and status != 'published':
                if not dry_run:
                    sub['status'] = 'published'
                    sub['updated_at'] = now_iso
                    hist = sub.get('status_history') or []
                    hist.append({'status': 'published', 'at': now_iso})
                    sub['status_history'] = hist
                    client.put_object(
                        Bucket='podcast-admin', Key=key,
                        Body=json.dumps(sub, indent=2) + "\n",
                        ContentType='application/json',
                    )
                advanced_in_r2 += 1
                print(f"  ADVANCE->published: {key} ({status})")
            elif action == 'delete':
                if not dry_run:
                    client.delete_object(Bucket='podcast-admin', Key=key)
                deleted_from_r2 += 1
                print(f"  DELETE: {key} ({status}, stem={basename})")

        except Exception as e:
            print(f"  Error processing {key}: {e}")

    verb = "Deleted" if not dry_run else "Would delete"
    print(f"  {verb} {deleted_from_r2} from R2; advanced {advanced_in_r2}")

    print(f"\nTotal: removed {deleted_from_sqlite + deleted_from_r2}, "
          f"advanced {advanced_in_sqlite + advanced_in_r2}")


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
