#!/usr/bin/env python3
"""Pick up pending submissions from R2 and generate drafts.

This worker discovers submissions in status 'submitted', claims them,
runs gen-podcast.py to produce a draft, then updates the submission
status to 'draft_generated' or 'generation_failed'.

Designed to run as a systemd oneshot service alongside the publish
worker, or standalone via CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r2_upload import get_r2_client


# ---------------------------------------------------------------------------
# R2 submission helpers
# ---------------------------------------------------------------------------

def _get_bucket_and_client():
    bucket = os.environ.get("ADMIN_BUCKET_NAME", "podcast-admin")
    client = get_r2_client()
    return bucket, client


def _list_submissions(bucket, client):
    """List all submission objects from R2."""
    submissions = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="submissions/"):
        for obj in page.get("Contents", []):
            try:
                body = client.get_object(Bucket=bucket, Key=obj["Key"])
                data = body["Body"].read()
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                sub = json.loads(data)
                sub["_key"] = obj["Key"]
                submissions.append(sub)
            except Exception:
                continue
    return submissions


def _update_submission(bucket, client, key, updates):
    """Read-modify-write a submission record in R2."""
    from datetime import datetime, timezone

    body = client.get_object(Bucket=bucket, Key=key)
    data = body["Body"].read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    sub = json.loads(data)

    now = datetime.now(timezone.utc).isoformat()
    sub.update(updates)
    sub["updated_at"] = now

    history = sub.get("status_history", [])
    entry = {"status": updates.get("status", sub.get("status")), "at": now}
    if "claimed_by" in updates:
        entry["by"] = updates["claimed_by"]
    history.append(entry)
    sub["status_history"] = history

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(sub, indent=2) + "\n",
        ContentType="application/json",
    )
    return sub


# ---------------------------------------------------------------------------
# Generation logic
# ---------------------------------------------------------------------------

def _find_pending(bucket, client):
    """Return submissions in 'submitted' or 'pending' status."""
    pending = []
    for sub in _list_submissions(bucket, client):
        status = sub.get("status", "pending")
        if status in ("submitted", "pending"):
            pending.append(sub)
    # Oldest first
    pending.sort(key=lambda s: s.get("timestamp", ""))
    return pending


def _claim_submission(bucket, client, sub, admin_id):
    """Transition a submission to generation_claimed."""
    return _update_submission(bucket, client, sub["_key"], {
        "status": "generation_claimed",
        "claimed_by": admin_id,
    })


def _run_generation(sub):
    """Run gen-podcast.py for the submission URLs.

    Returns (success, draft_stem_or_error).
    """
    urls = sub.get("urls", [])
    if not urls:
        return False, "No URLs in submission"

    cmd = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(ROOT / "gen-podcast.py"),
    ] + urls

    instructions = sub.get("instructions")
    if instructions:
        cmd += ["--goal", instructions]

    print(f"[gen-worker] Running: {' '.join(cmd[:4])}... ({len(urls)} URLs)")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout
        )
    except subprocess.TimeoutExpired:
        return False, "Generation timed out after 30 minutes"
    except Exception as e:
        return False, str(e)

    if result.returncode != 0:
        # Extract last few lines of stderr for the error message
        err_lines = (result.stderr or "").strip().splitlines()
        err_msg = "\n".join(err_lines[-5:]) if err_lines else "Unknown error"
        return False, err_msg

    # Try to extract the draft stem from stdout
    # gen-podcast.py prints the draft path on completion
    stdout_lines = (result.stdout or "").strip().splitlines()
    draft_stem = None
    for line in reversed(stdout_lines):
        if "drafts/" in line:
            # Extract path-like tokens
            for token in line.split():
                if "drafts/" in token:
                    # Strip extension
                    stem = token.rsplit(".", 1)[0] if "." in token else token
                    draft_stem = stem
                    break
            if draft_stem:
                break

    return True, draft_stem


def process_submission(sub, admin_id, *, bucket, client):
    """Claim, generate, and update status for one submission."""
    key = sub["_key"]
    print(f"[gen-worker] Claiming {key}")

    _claim_submission(bucket, client, sub, admin_id)
    _update_submission(bucket, client, key, {"status": "generation_running"})

    success, result = _run_generation(sub)

    if success:
        updates = {"status": "draft_generated"}
        if result:
            updates["draft_stem"] = result
        _update_submission(bucket, client, key, updates)
        print(f"[gen-worker] Draft generated for {key}")
        return True
    else:
        _update_submission(bucket, client, key, {
            "status": "generation_failed",
            "error": result[:500] if result else "Unknown error",
        })
        print(f"[gen-worker] Generation failed for {key}: {result[:200]}")
        return False


def run_once(admin_id: str) -> int:
    """Process one pending submission. Returns count processed."""
    bucket, client = _get_bucket_and_client()
    pending = _find_pending(bucket, client)
    if not pending:
        print("[gen-worker] No pending submissions")
        return 0

    sub = pending[0]
    process_submission(sub, admin_id, bucket=bucket, client=client)
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generation worker: pick up submitted papers and generate drafts."
    )
    parser.add_argument("--admin-id", required=True)
    parser.add_argument(
        "--once", action="store_true",
        help="Process one pending submission, then exit",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Process all pending submissions, then exit",
    )
    args = parser.parse_args()

    if not any((args.once, args.all)):
        parser.error("choose --once or --all")

    if args.once:
        return 0 if run_once(args.admin_id) >= 0 else 1

    if args.all:
        bucket, client = _get_bucket_and_client()
        pending = _find_pending(bucket, client)
        if not pending:
            print("[gen-worker] No pending submissions")
            return 0
        for sub in pending:
            process_submission(sub, args.admin_id, bucket=bucket, client=client)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
