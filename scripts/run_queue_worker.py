#!/usr/bin/env python3
"""Queue-refresh worker: run the editorial queue pipeline and upload results.

This is the dedicated automation entry point for queue refresh, intended
to run on its own systemd timer SEPARATE from the 2-minute podcast worker.

Each invocation:
  1. Checks that the requesting admin holds the ``queue_refresh``
     capability in the admin allowlist (config.yaml).
  2. Runs the full editorial queue pipeline (paper_queue.run_queue).
  3. Uploads queue.json to the podcast-admin R2 bucket as
     ``queue/latest.json`` so the admin panel can read it.
  4. Uploads queue.html and queue.xml to the public R2 bucket so the
     public site serves fresh queue pages.

Usage:
    python scripts/run_queue_worker.py --admin-id mcgrof --once
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUEUE_JSON_PATH = ROOT / "queue.json"
QUEUE_HTML_PATH = ROOT / "podcasts" / "queue.html"
QUEUE_XML_PATH = ROOT / "podcasts" / "queue.xml"
R2_ADMIN_BUCKET = "podcast-admin"
R2_PUBLIC_BUCKET = "ai-post-transformers"
R2_QUEUE_KEY = "queue/latest.json"

# Public queue artifacts: (local_path, r2_key, content_type)
PUBLIC_QUEUE_ARTIFACTS = [
    (QUEUE_HTML_PATH, "queue.html", "text/html"),
    (QUEUE_XML_PATH, "queue.xml", "application/xml"),
]


def _check_auth(admin_id: str) -> bool:
    """Verify admin_id holds queue_refresh capability."""
    from scripts.admin_allowlist import is_authorized
    return is_authorized(admin_id, "queue_refresh")


def _run_queue_refresh() -> bool:
    """Run the editorial queue pipeline. Returns True on success."""
    import yaml
    with open(ROOT / "config.yaml") as fh:
        config = yaml.safe_load(fh)

    from paper_queue import run_queue
    run_queue(config)
    return True


def _have_r2_creds() -> bool:
    """Return True if all required R2 env vars are set."""
    return all([
        os.environ.get("AWS_ENDPOINT_URL"),
        os.environ.get("AWS_ACCESS_KEY_ID"),
        os.environ.get("AWS_SECRET_ACCESS_KEY"),
    ])


def _upload_queue_json() -> str | None:
    """Upload queue.json to R2 admin bucket as queue/latest.json.

    Returns the R2 key on success, None if queue.json is missing or
    R2 credentials are not configured.
    """
    if not QUEUE_JSON_PATH.exists():
        print("[queue-worker] queue.json not found, skipping upload",
              file=sys.stderr)
        return None

    if not _have_r2_creds():
        print("[queue-worker] R2 credentials not set, skipping upload",
              file=sys.stderr)
        return None

    from r2_upload import get_r2_client, upload_file
    client = get_r2_client()
    upload_file(
        client,
        str(QUEUE_JSON_PATH),
        R2_QUEUE_KEY,
        content_type="application/json",
        bucket=R2_ADMIN_BUCKET,
    )
    return R2_QUEUE_KEY


def _upload_public_artifacts() -> list[str]:
    """Upload queue.html and queue.xml to the public R2 bucket.

    Returns list of R2 keys successfully uploaded.
    """
    if not _have_r2_creds():
        print("[queue-worker] R2 credentials not set, skipping "
              "public artifact upload", file=sys.stderr)
        return []

    from r2_upload import get_r2_client, upload_file
    client = get_r2_client()
    uploaded = []

    for local_path, r2_key, content_type in PUBLIC_QUEUE_ARTIFACTS:
        if not local_path.exists():
            print(f"[queue-worker] {local_path.name} not found, skipping",
                  file=sys.stderr)
            continue
        upload_file(
            client,
            str(local_path),
            r2_key,
            content_type=content_type,
            bucket=R2_PUBLIC_BUCKET,
        )
        uploaded.append(r2_key)

    return uploaded


def run_once(admin_id: str) -> int:
    """Run one queue-refresh cycle.

    Returns 1 if the queue was refreshed, 0 otherwise.
    """
    if not _check_auth(admin_id):
        print(f"[queue-worker] Admin '{admin_id}' is not authorized "
              f"for queue_refresh", file=sys.stderr)
        return 0

    print(f"[queue-worker] Starting queue refresh (admin={admin_id})",
          file=sys.stderr)

    try:
        _run_queue_refresh()
    except Exception as exc:
        print(f"[queue-worker] Queue refresh failed: {exc}",
              file=sys.stderr)
        return 0

    try:
        key = _upload_queue_json()
        if key:
            print(f"[queue-worker] Uploaded {key} to {R2_ADMIN_BUCKET}",
                  file=sys.stderr)
    except Exception as exc:
        print(f"[queue-worker] R2 admin upload failed: {exc}",
              file=sys.stderr)

    try:
        public_keys = _upload_public_artifacts()
        for k in public_keys:
            print(f"[queue-worker] Uploaded {k} to {R2_PUBLIC_BUCKET}",
                  file=sys.stderr)
    except Exception as exc:
        print(f"[queue-worker] R2 public upload failed: {exc}",
              file=sys.stderr)

    print("[queue-worker] Queue refresh complete", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Queue-refresh worker: editorial queue + R2 upload."
    )
    parser.add_argument("--admin-id", required=True,
                        help="Admin ID requesting the refresh")
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle then exit")
    parser.add_argument("--loop", action="store_true",
                        help="Loop indefinitely")
    parser.add_argument("--interval-seconds", type=int, default=21600,
                        help="Loop interval (default: 6 hours)")
    args = parser.parse_args()

    if not any((args.once, args.loop)):
        parser.error("choose --once or --loop")

    if args.once:
        run_once(args.admin_id)
        return 0

    while True:
        run_once(args.admin_id)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
