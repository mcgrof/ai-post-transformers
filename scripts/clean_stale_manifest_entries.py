#!/usr/bin/env python3
"""Clean up stale manifest entries that don't have corresponding database records.

Stale entries can accumulate if:
- Episodes are deleted or superseded
- Manifest and DB get out of sync due to bugs
- Entries are manually created without corresponding DB records

This script removes manifest entries whose episode_id no longer exists
in the database, preventing them from cluttering the drafts UI.

Usage:
    python scripts/clean_stale_manifest_entries.py          # dry-run
    python scripts/clean_stale_manifest_entries.py --execute  # actually clean
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually clean the manifest (default: dry-run)",
    )
    args = parser.parse_args()

    from db import get_connection, init_db

    # Get list of all episode IDs in the database
    conn = get_connection()
    init_db(conn)
    rows = conn.execute(
        "SELECT DISTINCT id FROM podcasts ORDER BY id"
    ).fetchall()
    db_episode_ids = {row["id"] for row in rows}
    conn.close()

    # Read manifest from R2
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["AWS_ENDPOINT_URL"],
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        obj = s3.get_object(Bucket="podcast-admin", Key="manifest.json")
        manifest = json.loads(obj["Body"].read())
    except Exception as e:
        print(f"Error reading manifest: {e}", file=sys.stderr)
        sys.exit(1)

    drafts = manifest.get("drafts", [])
    stale = [d for d in drafts if d.get("id") not in db_episode_ids]

    if not stale:
        print(f"✓ Manifest is clean: all {len(drafts)} entries have DB records")
        return

    print(f"Found {len(stale)} stale manifest entries (out of {len(drafts)} total):")
    for entry in stale:
        title = entry.get("title", "(untitled)")
        ep_id = entry.get("id", "?")
        draft_key = entry.get("draft_key", "?")
        print(f"  - ID {ep_id}: {title}")
        print(f"    draft_key: {draft_key}")

    if args.execute:
        # Remove stale entries and rewrite manifest
        cleaned = [d for d in drafts if d.get("id") in db_episode_ids]
        manifest["drafts"] = cleaned

        try:
            s3.put_object(
                Bucket="podcast-admin",
                Key="manifest.json",
                Body=json.dumps(manifest, indent=2) + "\n",
                ContentType="application/json",
            )
            print(
                f"\n✓ Manifest cleaned: removed {len(stale)} "
                f"stale entries ({len(cleaned)} valid entries remain)"
            )
        except Exception as e:
            print(f"\nError writing manifest: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(
            f"\n[DRY-RUN] Would remove {len(stale)} stale entries. "
            "Run with --execute to apply."
        )


if __name__ == "__main__":
    main()
