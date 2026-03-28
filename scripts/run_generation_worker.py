#!/usr/bin/env python3
"""Pick up pending submissions from R2 and generate drafts.

This worker discovers submissions in status 'submitted', claims them,
runs gen-podcast.py to produce a draft, then updates the submission
status to 'draft_generated' or 'generation_failed'.

Designed to run as a systemd oneshot service alongside the publish
worker, or standalone via CLI.

Hardening:
- Each claim writes a unique claim_token (UUID4) and a lease_expires_at
  timestamp.  Before writing terminal status the worker re-reads the
  record and verifies the token still matches, preventing a second
  worker from silently clobbering a reassigned submission.
- Stale claims (lease expired) are detected and released back to
  'submitted' before any new work is picked up.
- An admin with an already-active generation_running submission is
  blocked from claiming another, avoiding duplicate work.
- Long-running gen-podcast.py subprocesses are wrapped in a heartbeat
  loop that periodically extends the lease.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r2_upload import get_r2_client

GENERATION_LEASE_SECONDS = 1800
HEARTBEAT_INTERVAL_SECONDS = 300
GENERATION_CLAIMABLE_STATES = ("submitted", "pending")
GENERATION_ACTIVE_STATES = ("generation_claimed", "generation_running")


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


def _read_submission(bucket, client, key):
    """Read a single submission record from R2."""
    body = client.get_object(Bucket=bucket, Key=key)
    data = body["Body"].read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    sub = json.loads(data)
    sub["_key"] = key
    return sub


def _update_submission(bucket, client, key, updates):
    """Read-modify-write a submission record in R2."""
    sub = _read_submission(bucket, client, key)

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


def _lease_is_active(sub, now=None):
    """Return True if the submission's lease has not yet expired."""
    now = now or datetime.now(timezone.utc)
    expires = sub.get("lease_expires_at")
    if not expires:
        return False
    return datetime.fromisoformat(expires) > now


# ---------------------------------------------------------------------------
# Stale-claim and duplicate-run protection
# ---------------------------------------------------------------------------

def _release_stale_claims(bucket, client):
    """Reset submissions with expired generation leases back to submitted.

    Scans all submissions in generation_claimed or generation_running
    state.  If the lease has expired the record is re-read (guard against
    races) and, if still stale, released.
    """
    released = 0
    for sub in _list_submissions(bucket, client):
        status = sub.get("status")
        if status not in GENERATION_ACTIVE_STATES:
            continue
        if _lease_is_active(sub):
            continue
        # No lease_expires_at at all means a legacy record that predates
        # lease support — treat it as stale only if it has been claimed.
        if not sub.get("lease_expires_at") and not sub.get("claim_token"):
            continue

        key = sub["_key"]
        fresh = _read_submission(bucket, client, key)
        if fresh.get("status") not in GENERATION_ACTIVE_STATES:
            continue
        if _lease_is_active(fresh):
            continue

        owner = fresh.get("claimed_by", "unknown")
        _update_submission(bucket, client, key, {
            "status": "submitted",
            "claimed_by": None,
            "claim_token": None,
            "lease_expires_at": None,
            "last_heartbeat_at": None,
            "release_reason": "lease expired during generation",
        })
        released += 1
        print(
            f"[gen-worker] Released stale claim on {key} "
            f"(was claimed by {owner})"
        )
    return released


def _active_generation_for_admin(bucket, client, admin_id):
    """Return submissions actively being generated by *admin_id*."""
    active = []
    for sub in _list_submissions(bucket, client):
        if sub.get("status") not in GENERATION_ACTIVE_STATES:
            continue
        if sub.get("claimed_by") != admin_id:
            continue
        if not _lease_is_active(sub):
            continue
        active.append(sub)
    return active


# ---------------------------------------------------------------------------
# Generation logic
# ---------------------------------------------------------------------------

def _find_pending(bucket, client):
    """Return submissions in 'submitted' or 'pending' status."""
    pending = []
    for sub in _list_submissions(bucket, client):
        status = sub.get("status", "pending")
        if status in GENERATION_CLAIMABLE_STATES:
            pending.append(sub)
    # Oldest first
    pending.sort(key=lambda s: s.get("timestamp", ""))
    return pending


def _claim_submission(bucket, client, sub, admin_id,
                      lease_seconds=GENERATION_LEASE_SECONDS):
    """Transition a submission to generation_claimed with a lease."""
    now = datetime.now(timezone.utc)
    token = str(uuid.uuid4())
    return _update_submission(bucket, client, sub["_key"], {
        "status": "generation_claimed",
        "claimed_by": admin_id,
        "claim_token": token,
        "lease_expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
        "last_heartbeat_at": now.isoformat(),
    })


def _heartbeat_submission(bucket, client, key, claim_token,
                          lease_seconds=GENERATION_LEASE_SECONDS):
    """Extend the lease on a claimed submission.

    Re-reads the record and verifies the claim_token still matches
    before writing, so a reassigned submission is never silently
    clobbered.  Returns the refreshed record or None if the token
    no longer matches.
    """
    fresh = _read_submission(bucket, client, key)
    if fresh.get("claim_token") != claim_token:
        return None
    now = datetime.now(timezone.utc)
    _update_submission(bucket, client, key, {
        "lease_expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
        "last_heartbeat_at": now.isoformat(),
    })
    return fresh


def _run_generation(sub, *, bucket=None, client=None, claim_token=None,
                    key=None):
    """Run gen-podcast.py for the submission URLs.

    When bucket/client/claim_token/key are provided the subprocess is
    wrapped in a heartbeat loop that renews the lease periodically.

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

    can_heartbeat = all((bucket, client, claim_token, key))

    if can_heartbeat:
        return _run_generation_with_heartbeat(
            cmd, bucket=bucket, client=client,
            claim_token=claim_token, key=key,
        )

    # Fallback: simple blocking run (tests, or missing context)
    return _run_generation_simple(cmd)


def _run_generation_simple(cmd):
    """Blocking subprocess without heartbeat."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False, "Generation timed out after 30 minutes"
    except Exception as e:
        return False, str(e)

    return _parse_generation_result(result)


def _run_generation_with_heartbeat(cmd, *, bucket, client, claim_token, key):
    """Run gen-podcast.py with periodic lease heartbeats."""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        return False, str(e)

    while True:
        try:
            proc.wait(timeout=HEARTBEAT_INTERVAL_SECONDS)
        except subprocess.TimeoutExpired:
            renewed = _heartbeat_submission(
                bucket, client, key, claim_token,
            )
            if renewed is None:
                print(
                    f"[gen-worker] Claim token mismatch for {key}; "
                    "another worker may have reclaimed it"
                )
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False, "Claim lost to another worker"
            continue

        # Process finished
        stdout = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""

        class _Result:
            pass

        r = _Result()
        r.returncode = proc.returncode
        r.stdout = stdout
        r.stderr = stderr
        return _parse_generation_result(r)


def _parse_generation_result(result):
    """Extract success/failure and draft stem from a completed process."""
    if result.returncode != 0:
        err_lines = (result.stderr or "").strip().splitlines()
        err_msg = "\n".join(err_lines[-5:]) if err_lines else "Unknown error"
        return False, err_msg

    stdout_lines = (result.stdout or "").strip().splitlines()
    draft_stem = None
    for line in reversed(stdout_lines):
        if "drafts/" in line:
            for token in line.split():
                if "drafts/" in token:
                    stem = token.rsplit(".", 1)[0] if "." in token else token
                    draft_stem = stem
                    break
            if draft_stem:
                break

    return True, draft_stem


def _verify_claim_token(bucket, client, key, expected_token):
    """Re-read the submission and confirm the claim_token matches.

    Returns True if the token is still ours, False otherwise.
    """
    fresh = _read_submission(bucket, client, key)
    return fresh.get("claim_token") == expected_token


def process_submission(sub, admin_id, *, bucket, client):
    """Claim, generate, and update status for one submission."""
    key = sub["_key"]
    print(f"[gen-worker] Claiming {key}")

    claimed = _claim_submission(bucket, client, sub, admin_id)
    claim_token = claimed.get("claim_token")

    _update_submission(bucket, client, key, {"status": "generation_running"})

    success, result = _run_generation(
        sub, bucket=bucket, client=client,
        claim_token=claim_token, key=key,
    )

    if not _verify_claim_token(bucket, client, key, claim_token):
        print(
            f"[gen-worker] Claim token mismatch after generation for "
            f"{key}; skipping status update"
        )
        return False

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


def run_once(admin_id: str, *, bucket=None, client=None) -> int:
    """Process one pending submission. Returns count processed."""
    if bucket is None or client is None:
        bucket, client = _get_bucket_and_client()

    _release_stale_claims(bucket, client)

    active = _active_generation_for_admin(bucket, client, admin_id)
    if active:
        active_keys = ", ".join(s["_key"] for s in active)
        print(
            f"[gen-worker] Active generation already running for "
            f"{admin_id}: {active_keys}; skipping new claim"
        )
        return 0

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

        _release_stale_claims(bucket, client)

        active = _active_generation_for_admin(bucket, client, args.admin_id)
        if active:
            active_keys = ", ".join(s["_key"] for s in active)
            print(
                f"[gen-worker] Active generation already running for "
                f"{args.admin_id}: {active_keys}; skipping"
            )
            return 0

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
