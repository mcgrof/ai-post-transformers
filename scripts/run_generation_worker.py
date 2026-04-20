#!/usr/bin/env python3
"""Pick up pending submissions and generate drafts.

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

Storage backends:
- QueueStore (SQLite): Transactional, default when --queue-db is set.
  All mutations use BEGIN IMMEDIATE + version-based CAS.
- R2 (legacy): Direct read-modify-write on Cloudflare R2 objects.
  Used when --queue-db is not set and R2 credentials are available.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GENERATION_LEASE_SECONDS = 1800
HEARTBEAT_INTERVAL_SECONDS = 300
GENERATION_CLAIMABLE_STATES = ("submitted", "pending")
GENERATION_ACTIVE_STATES = ("generation_claimed", "generation_running")


# ---------------------------------------------------------------------------
# R2 submission helpers (legacy — used when no QueueStore is configured)
# ---------------------------------------------------------------------------

def _get_bucket_and_client():
    from r2_upload import get_r2_client
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
# R2 stale-claim and duplicate-run protection
# ---------------------------------------------------------------------------

def _release_stale_claims(bucket, client):
    released = 0
    for sub in _list_submissions(bucket, client):
        status = sub.get("status")
        if status not in GENERATION_ACTIVE_STATES:
            continue
        if _lease_is_active(sub):
            continue
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

def _submission_source_text(sub, url):
    metadata = (sub.get("metadata") or {}).get(url) or {}
    for key in ("fallback_source_text", "manual_source_text", "summary_text"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _prepare_generation_inputs(sub):
    urls = sub.get("urls", [])
    prepared = []
    temp_paths = []

    for url in urls:
        fallback_text = _submission_source_text(sub, url)
        if not fallback_text:
            prepared.append(url)
            continue

        metadata = (sub.get("metadata") or {}).get(url) or {}
        title = metadata.get("title") or sub.get("title") or ""
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="submission-source-",
            delete=False,
        )
        if title:
            tmp.write(f"Title: {title}\n\n")
        tmp.write(f"Source URL: {url}\n\n")
        tmp.write(fallback_text)
        tmp.write("\n")
        tmp.close()

        prepared.append(tmp.name)
        temp_paths.append(Path(tmp.name))
        print(f"[gen-worker] Using fallback source text for {url} -> {tmp.name}")

    return prepared, temp_paths


def _find_pending(bucket, client):
    pending = []
    for sub in _list_submissions(bucket, client):
        status = sub.get("status", "pending")
        if status in GENERATION_CLAIMABLE_STATES:
            pending.append(sub)
    pending.sort(key=lambda s: s.get("timestamp", ""))
    return pending


def _claim_submission(bucket, client, sub, admin_id,
                      lease_seconds=GENERATION_LEASE_SECONDS):
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
                    key=None, store=None):
    """Run gen-podcast.py for the submission URLs.

    When heartbeat context is provided (either R2 bucket/client or a
    QueueStore) the subprocess is wrapped in a heartbeat loop that
    renews the lease periodically.

    Returns (success, draft_stem_or_error).
    """
    urls = sub.get("urls", [])
    if not urls:
        return False, "No URLs in submission"

    prepared_inputs, temp_paths = _prepare_generation_inputs(sub)

    cmd = [
        str(ROOT / ".venv" / "bin" / "python"),
        str(ROOT / "gen-podcast.py"),
    ] + prepared_inputs

    instructions = sub.get("instructions")
    if instructions:
        cmd += ["--goal", instructions]

    print(f"[gen-worker] Running: {' '.join(cmd[:4])}... ({len(prepared_inputs)} URLs)")

    can_heartbeat_r2 = all((bucket, client, claim_token, key))
    can_heartbeat_store = all((store, claim_token, key))

    try:
        if can_heartbeat_store:
            return _run_generation_with_heartbeat_store(
                cmd, store=store, claim_token=claim_token, key=key,
            )

        if can_heartbeat_r2:
            return _run_generation_with_heartbeat(
                cmd, bucket=bucket, client=client,
                claim_token=claim_token, key=key,
            )

        # Fallback: simple blocking run (tests, or missing context)
        return _run_generation_simple(cmd)
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


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
    """Run gen-podcast.py with periodic lease heartbeats via R2."""
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


def _run_generation_with_heartbeat_store(cmd, *, store, claim_token, key):
    """Run gen-podcast.py with periodic lease heartbeats via QueueStore."""
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
            renewed = store.heartbeat_submission(key, claim_token)
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

    draft_stem = None
    output_lines = []
    if result.stdout:
        output_lines.extend((result.stdout or "").strip().splitlines())
    if result.stderr:
        output_lines.extend((result.stderr or "").strip().splitlines())

    for line in reversed(output_lines):
        if "drafts/" in line:
            for token in line.split():
                if "drafts/" in token:
                    stem = token.rsplit(".", 1)[0] if "." in token else token
                    draft_stem = stem
                    break
            if draft_stem:
                break

    # Normalize absolute filesystem paths to relative R2 keys.
    # gen-podcast.py prints absolute paths like
    # /home/.../drafts/2026/04/slug but submissions and the admin
    # UI expect relative paths like drafts/2026/04/slug.
    if draft_stem:
        idx = draft_stem.find("drafts/")
        if idx > 0:
            draft_stem = draft_stem[idx:]

    return True, draft_stem


def _verify_claim_token(bucket, client, key, expected_token):
    fresh = _read_submission(bucket, client, key)
    return fresh.get("claim_token") == expected_token


def _create_private_publish_job(*, draft_stem: str, sub: dict,
                                owner: str) -> str:
    """Create a visibility=private publish job in R2.

    Called by the generation worker when a private submission finishes
    generation.  The publish worker will pick this up and route it via
    process_private_job → gen-podcast.py publish --private, uploading
    the episode under private-episodes/{token}/ in the admin bucket.
    """
    from scripts.publish_jobs import make_job_record
    from scripts.publish_job_store import get_publish_job_store

    _local_stem, remote_stem = _draft_stem_local_and_remote(draft_stem)
    draft_key = f"{remote_stem}.mp3"
    title = sub.get("title") or Path(remote_stem).name

    job = make_job_record(
        draft_key=draft_key,
        draft_stem=remote_stem,
        title=title,
        approved_by_admin_id=owner,
        approved_by_name=owner,
    )
    # Mark the job private + owner-scoped BEFORE saving. The runner's
    # dispatch checks job.visibility to route to process_private_job.
    job["visibility"] = "private"
    job["owner"] = owner

    r2_store = get_publish_job_store(mode="r2")
    r2_store.save_job(job)
    return job["job_id"]


def _draft_stem_local_and_remote(draft_stem: str) -> tuple[Path, str]:
    stem_path = Path(draft_stem)
    local_stem = stem_path if stem_path.is_absolute() else ROOT / stem_path

    if stem_path.is_absolute():
        try:
            remote_stem = str(stem_path.relative_to(ROOT)).replace(os.sep, "/")
        except ValueError:
            remote_stem = f"drafts/{stem_path.name}"
    else:
        remote_stem = draft_stem.replace(os.sep, "/")

    return local_stem, remote_stem


def _publish_draft_metadata(draft_stem: str) -> None:
    """Push draft metadata to the admin manifest + enrich sidecar JSON.

    Best-effort: failures are logged but do not block the generation
    pipeline.  The admin UI can still fall back to sidecar JSON or
    displayTitle() heuristics.
    """
    if not draft_stem:
        return

    local_stem, remote_stem = _draft_stem_local_and_remote(draft_stem)

    try:
        from db import get_connection, init_db
        conn = get_connection()
        init_db(conn)

        # Find episode by matching audio_file to the draft stem
        rows = conn.execute(
            "SELECT * FROM podcasts WHERE audio_file LIKE ? "
            "ORDER BY id DESC LIMIT 1",
            (f"%{Path(draft_stem).name}%",),
        ).fetchall()
        if not rows:
            print(
                f"[gen-worker] No DB episode found for draft stem "
                f"{draft_stem}; skipping manifest update"
            )
            conn.close()
            return

        ep = dict(rows[0])
        conn.close()

        from scripts.draft_manifest import (
            build_manifest_entry,
            upsert_manifest_draft,
            enrich_sidecar_json,
        )

        entry = build_manifest_entry(
            ep, draft_key=f"{remote_stem}.mp3", draft_stem=remote_stem,
        )
        upsert_manifest_draft(entry)
        print(f"[gen-worker] Manifest updated for ep {ep['id']}: {ep.get('title', '?')}")

        # Enrich sidecar JSON so admin fallback works too
        sidecar = Path(f"{local_stem}.json")
        source_urls = ep.get("source_urls")
        if isinstance(source_urls, str):
            try:
                source_urls = json.loads(source_urls)
            except (json.JSONDecodeError, TypeError):
                source_urls = []

        enrich_sidecar_json(
            sidecar,
            title=ep.get("title"),
            description=ep.get("description"),
            source_urls=source_urls,
            episode_id=ep["id"],
        )

    except Exception as exc:
        print(f"[gen-worker] Draft metadata publish failed (non-fatal): {exc}")


def _upload_draft_artifacts(draft_stem: str) -> tuple[bool, dict]:
    """Upload freshly generated draft artifacts to the podcast bucket.

    Returns (ok, details). The MP3 is required for a draft to be
    immediately reviewable/playable. Sibling assets are best-effort and
    surface as warnings without failing the generation.
    """
    if not draft_stem:
        return False, {"error": "generation completed without a draft stem"}

    local_stem, remote_stem = _draft_stem_local_and_remote(draft_stem)
    required_mp3 = Path(f"{local_stem}.mp3")
    if not required_mp3.exists():
        return False, {"error": f"draft MP3 missing after generation: {required_mp3}"}

    from r2_upload import get_r2_client, upload_file

    client = get_r2_client()
    uploaded = {}
    warnings = []

    artifact_plan = [
        ("mp3", ".mp3", True),
        ("srt", ".srt", False),
        ("cover", ".png", False),
        ("transcript", ".txt", False),
        ("metadata", ".json", False),
    ]

    for label, suffix, required in artifact_plan:
        local_path = Path(f"{local_stem}{suffix}")
        if not local_path.exists():
            if required:
                return False, {"error": f"required draft artifact missing: {local_path}"}
            warnings.append(f"missing optional artifact: {local_path.name}")
            continue

        r2_key = f"{remote_stem}{suffix}"
        try:
            uploaded[label] = upload_file(client, str(local_path), r2_key)
        except Exception as exc:
            if required:
                return False, {"error": f"failed to upload {r2_key}: {exc}"}
            warnings.append(f"failed to upload {r2_key}: {exc}")

    details = {
        "draft_artifacts": uploaded,
        "draft_uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if warnings:
        details["draft_upload_warnings"] = warnings
    return True, details


# ---------------------------------------------------------------------------
# QueueStore-based process_submission
# ---------------------------------------------------------------------------

def _process_submission_store(sub, admin_id, *, store):
    """Claim, generate, and update status using a transactional QueueStore."""
    key = sub["_key"]
    print(f"[gen-worker] Claiming {key} (store)")

    claimed = store.claim_submission(key, admin_id)
    if claimed is None:
        print(f"[gen-worker] Could not claim {key}")
        return False
    claim_token = claimed["claim_token"]

    store.update_submission(key, {"status": "generation_running"})

    success, result = _run_generation(
        sub, store=store, claim_token=claim_token, key=key,
    )

    if not store.verify_claim_token(key, claim_token):
        print(
            f"[gen-worker] Claim token mismatch after generation for "
            f"{key}; skipping status update"
        )
        return False

    if success:
        if not result:
            store.update_submission(key, {
                "status": "generation_failed",
                "error": "generation finished but produced no draft stem",
            })
            print(f"[gen-worker] No draft stem in output for {key}")
            return False
        updates = {"status": "draft_generated", "draft_stem": result}
        upload_ok, upload_details = _upload_draft_artifacts(result)
        if not store.verify_claim_token(key, claim_token):
            print(
                f"[gen-worker] Claim token mismatch after draft upload for "
                f"{key}; skipping status update"
            )
            return False
        if not upload_ok:
            store.update_submission(key, {
                "status": "generation_failed",
                "error": upload_details.get("error", "draft upload failed")[:500],
            })
            print(
                f"[gen-worker] Draft upload failed for {key}: "
                f"{upload_details.get('error', 'unknown error')}"
            )
            return False
        updates.update(upload_details)
        _publish_draft_metadata(result)
        store.update_submission(key, updates)
        print(f"[gen-worker] Draft generated for {key}")

        # Auto-route private submissions to the private publish flow.
        # Without this, private drafts get stuck: they're filtered out
        # of the public /drafts review page (safety) but nothing creates
        # a publish job, so the episode never reaches /private-podcasts.
        try:
            if sub.get("visibility") == "private":
                owner = sub.get("owner") or "admin"
                _create_private_publish_job(
                    draft_stem=result,
                    sub=sub,
                    owner=owner,
                )
                # Advance submission state so the admin UI shows that
                # it's been sent to the private publish lane.
                store.update_submission(key, {
                    "status": "approved_for_publish",
                })
                print(
                    f"[gen-worker] Private publish job created for {key} "
                    f"(owner={owner})"
                )
        except Exception as exc:
            # Auto-publish is best-effort. If it fails the operator can
            # retry via the admin UI's "Move to Private" button.
            print(
                f"[gen-worker] WARNING: failed to auto-create private "
                f"publish job for {key}: {exc}"
            )
        return True
    else:
        store.update_submission(key, {
            "status": "generation_failed",
            "error": result[:500] if result else "Unknown error",
        })
        print(f"[gen-worker] Generation failed for {key}: {result[:200]}")
        return False


# ---------------------------------------------------------------------------
# R2-based process_submission (legacy)
# ---------------------------------------------------------------------------

def process_submission(sub, admin_id, *, bucket=None, client=None,
                       store=None):
    """Claim, generate, and update status for one submission.

    When *store* is provided, uses the transactional QueueStore path.
    Otherwise falls back to R2 read-modify-write.
    """
    if store is not None:
        return _process_submission_store(sub, admin_id, store=store)

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
        if not result:
            _update_submission(bucket, client, key, {
                "status": "generation_failed",
                "error": "generation finished but produced no draft stem",
            })
            print(f"[gen-worker] No draft stem in output for {key}")
            return False
        updates = {"status": "draft_generated", "draft_stem": result}
        upload_ok, upload_details = _upload_draft_artifacts(result)
        if not _verify_claim_token(bucket, client, key, claim_token):
            print(
                f"[gen-worker] Claim token mismatch after draft upload for "
                f"{key}; skipping status update"
            )
            return False
        if not upload_ok:
            _update_submission(bucket, client, key, {
                "status": "generation_failed",
                "error": upload_details.get("error", "draft upload failed")[:500],
            })
            print(
                f"[gen-worker] Draft upload failed for {key}: "
                f"{upload_details.get('error', 'unknown error')}"
            )
            return False
        updates.update(upload_details)
        _publish_draft_metadata(result)
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


# ---------------------------------------------------------------------------
# run_once — dispatches to QueueStore or R2
# ---------------------------------------------------------------------------

def _run_once_store(admin_id: str, *, store) -> int:
    """Process one pending submission via QueueStore."""
    store.release_stale_submissions()

    active = store.active_submissions_for_admin(admin_id)
    if active:
        active_keys = ", ".join(s["_key"] for s in active)
        print(
            f"[gen-worker] Active generation already running for "
            f"{admin_id}: {active_keys}; skipping new claim"
        )
        return 0

    pending = store.find_pending_submissions()
    if not pending:
        print("[gen-worker] No pending submissions")
        return 0

    sub = pending[0]
    _process_submission_store(sub, admin_id, store=store)
    return 1


def run_once(admin_id: str, *, bucket=None, client=None,
             store=None) -> int:
    """Process one pending submission. Returns count processed.

    When *store* is provided, uses the transactional QueueStore.
    Otherwise falls back to R2.
    """
    if store is not None:
        return _run_once_store(admin_id, store=store)

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
        description="Generation worker: pick up submitted papers "
                    "and generate drafts."
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
    parser.add_argument(
        "--queue-db",
        help="Path to SQLite queue database (enables QueueStore mode)",
    )
    args = parser.parse_args()

    if not any((args.once, args.all)):
        parser.error("choose --once or --all")

    store = None
    if args.queue_db:
        from scripts.queue_store import SQLiteQueueStore
        store = SQLiteQueueStore(args.queue_db)

    if args.once:
        return 0 if run_once(args.admin_id, store=store) >= 0 else 1

    if args.all:
        if store is not None:
            store.release_stale_submissions()
            active = store.active_submissions_for_admin(args.admin_id)
            if active:
                active_keys = ", ".join(s["_key"] for s in active)
                print(
                    f"[gen-worker] Active generation already running for "
                    f"{args.admin_id}: {active_keys}; skipping"
                )
                return 0
            pending = store.find_pending_submissions()
            if not pending:
                print("[gen-worker] No pending submissions")
                return 0
            for sub in pending:
                _process_submission_store(
                    sub, args.admin_id, store=store
                )
            return 0

        bucket, client = _get_bucket_and_client()

        _release_stale_claims(bucket, client)

        active = _active_generation_for_admin(
            bucket, client, args.admin_id
        )
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
            process_submission(
                sub, args.admin_id, bucket=bucket, client=client
            )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
