#!/usr/bin/env python3
"""Bootstrap admins.json in the admin R2 bucket.

Run once when first deploying the admin-management UI. After this, the
first authenticated user (or any user already in the file) can manage
the list via the /admins page in the admin worker.

Usage:
    .venv/bin/python scripts/init_admins.py
    .venv/bin/python scripts/init_admins.py --email me@gmail.com
    .venv/bin/python scripts/init_admins.py --dry-run

Reads R2 credentials from environment variables that the rest of the
project already uses (AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY). Falls back to the admin config in config.yaml
for the bootstrap admin id if --email is not provided.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3

ADMIN_BUCKET = "podcast-admin"
ADMINS_KEY = "admins.json"
CAPABILITIES = ("admin", "manage_admins", "publish", "queue_refresh")


def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "auto"),
    )


def _resolve_default_email() -> str | None:
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml
    except ImportError:
        return None
    with cfg_path.open() as fh:
        cfg = yaml.safe_load(fh) or {}
    admins = ((cfg.get("admin") or {}).get("admins") or [])
    if not admins:
        return None
    # config.yaml stores `id` (local part), not full email. Caller
    # should pass --email explicitly for a non-gmail bootstrap.
    aid = admins[0].get("id")
    return f"{aid}@gmail.com" if aid else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email",
                        help="Bootstrap admin email (defaults to "
                             "<first admin id from config.yaml>@gmail.com)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written; do not PUT")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing admins.json")
    args = parser.parse_args()

    email = (args.email or _resolve_default_email() or "").strip()
    if not email:
        print("Could not determine bootstrap email. Pass --email.",
              file=sys.stderr)
        return 2

    s3 = _r2_client()
    existing = None
    try:
        obj = s3.get_object(Bucket=ADMIN_BUCKET, Key=ADMINS_KEY)
        existing = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        existing = None
    except Exception as e:
        msg = str(e)
        if "NoSuchKey" in msg or "404" in msg:
            existing = None
        else:
            raise

    if existing and existing.get("admins") and not args.force:
        print(f"admins.json already has "
              f"{len(existing['admins'])} entries; refusing to clobber. "
              f"Use --force to overwrite.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).isoformat()
    body = {
        "version": 1,
        "admins": [
            {
                "email": email,
                "capabilities": list(CAPABILITIES),
                "added_by": "system",
                "added_at": now,
                "notes": "bootstrap admin",
            }
        ],
    }
    payload = json.dumps(body, indent=2)
    print(f"Will write admins.json with bootstrap email {email}:")
    print(payload)
    if args.dry_run:
        print("\n[dry-run] not writing")
        return 0
    s3.put_object(
        Bucket=ADMIN_BUCKET, Key=ADMINS_KEY,
        Body=payload.encode("utf-8"),
        ContentType="application/json",
    )
    print(f"\nWrote {ADMIN_BUCKET}/{ADMINS_KEY}.")
    print("Next steps:")
    print("  1. Confirm /admins renders the bootstrap admin and the "
          "'Add admin' form in the live worker.")
    print("  2. (Optional) Set the CF Access integration secrets so "
          "adding admins via the UI also updates the CF Access policy:")
    print("       wrangler secret put CF_API_TOKEN")
    print("       (and CF_ACCOUNT_ID / CF_ACCESS_APP_UUID / "
          "CF_ACCESS_POLICY_UUID as wrangler vars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
