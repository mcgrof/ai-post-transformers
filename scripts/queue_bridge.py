#!/usr/bin/env python3
"""Bridge queue state between R2 and the local SQLite QueueStore.

This keeps R2 as the admin/UI control-plane surface while the local
worker uses SQLite for transactional claims and leases.

The bridge intentionally stays simple:

- import newer R2 submissions/publish jobs into SQLite before a worker run
- export newer SQLite submissions/publish jobs back to R2 after a run
- use updated_at/timestamp comparison to avoid obvious stale clobbers

It is not a distributed consensus system. It is the pragmatic shim that
lets a single worker host use SQLite safely while the admin worker still
reads and writes R2.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r2_upload import get_r2_client
from scripts.publish_job_store import R2PublishJobStore, get_publish_job_store
from scripts.queue_store import SQLiteQueueStore, get_queue_store

DEFAULT_ADMIN_BUCKET = os.environ.get("ADMIN_BUCKET_NAME", "podcast-admin")
SUBMISSIONS_PREFIX = "submissions/"


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_updated_at(record: dict) -> datetime:
    return _parse_timestamp(
        record.get("updated_at")
        or record.get("timestamp")
        or record.get("created_at")
    )


def _record_iso_updated_at(record: dict) -> str:
    value = record.get("updated_at") or record.get("timestamp") or record.get("created_at")
    if value:
        return _parse_timestamp(value).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _normalize_submission_for_store(key: str, submission: dict) -> dict:
    result = deepcopy(submission)
    result.pop("_key", None)
    result.setdefault("updated_at", _record_iso_updated_at(result))
    result.setdefault("timestamp", result["updated_at"])
    return result


def _normalize_job_for_store(job: dict) -> dict:
    result = deepcopy(job)
    result.setdefault("updated_at", _record_iso_updated_at(result))
    return result


def _normalize_submission_for_r2(submission: dict) -> dict:
    result = deepcopy(submission)
    result.pop("_key", None)
    result.setdefault("updated_at", _record_iso_updated_at(result))
    return result


def _list_submission_keys(*, bucket: str, client) -> list[str]:
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=SUBMISSIONS_PREFIX):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return sorted(keys)


def _read_submission(*, bucket: str, client, key: str) -> dict:
    obj = client.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


def _write_submission(*, bucket: str, client, key: str, submission: dict) -> str:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(submission, indent=2, sort_keys=True) + "\n",
        ContentType="application/json",
    )
    return key


def sync_submissions_from_r2(
    store,
    *,
    bucket: str | None = None,
    client=None,
) -> dict:
    bucket = bucket or DEFAULT_ADMIN_BUCKET
    client = client or get_r2_client()
    counts = {"scanned": 0, "imported": 0, "skipped": 0}

    for key in _list_submission_keys(bucket=bucket, client=client):
        counts["scanned"] += 1
        remote = _normalize_submission_for_store(
            key,
            _read_submission(bucket=bucket, client=client, key=key),
        )
        local_record = store.load_submission(key)
        if local_record is not None:
            local, _ = local_record
            if _record_updated_at(remote) <= _record_updated_at(local):
                counts["skipped"] += 1
                continue
        store.save_submission(key, remote)
        counts["imported"] += 1

    return counts


def sync_submissions_to_r2(
    store,
    *,
    bucket: str | None = None,
    client=None,
) -> dict:
    bucket = bucket or DEFAULT_ADMIN_BUCKET
    client = client or get_r2_client()
    counts = {"scanned": 0, "exported": 0, "skipped": 0}

    for submission in store.list_submissions():
        counts["scanned"] += 1
        key = submission["_key"]
        local = _normalize_submission_for_r2(submission)
        try:
            remote = _read_submission(bucket=bucket, client=client, key=key)
        except Exception as exc:
            if "NoSuchKey" not in str(exc) and "404" not in str(exc):
                raise
            remote = None

        if remote is not None and _record_updated_at(local) <= _record_updated_at(remote):
            counts["skipped"] += 1
            continue

        _write_submission(bucket=bucket, client=client, key=key, submission=local)
        counts["exported"] += 1

    return counts


def sync_publish_jobs_from_r2(
    store,
    *,
    r2_store=None,
) -> dict:
    r2_store = r2_store or get_publish_job_store(mode="r2")
    counts = {"scanned": 0, "imported": 0, "skipped": 0}

    for remote_job in r2_store.list_jobs():
        counts["scanned"] += 1
        remote = _normalize_job_for_store(remote_job)
        try:
            local = store.load_job(remote["job_id"])
        except KeyError:
            local = None
        if local is not None and _record_updated_at(remote) <= _record_updated_at(local):
            counts["skipped"] += 1
            continue
        store.save_job(remote)
        counts["imported"] += 1

    return counts


def sync_publish_jobs_to_r2(
    store,
    *,
    r2_store=None,
) -> dict:
    r2_store = r2_store or get_publish_job_store(mode="r2")
    counts = {"scanned": 0, "exported": 0, "skipped": 0}

    for local_job in store.list_jobs():
        counts["scanned"] += 1
        local = _normalize_job_for_store(local_job)
        try:
            remote = r2_store.load_job(local["job_id"])
        except KeyError:
            remote = None
        if remote is not None and _record_updated_at(local) <= _record_updated_at(remote):
            counts["skipped"] += 1
            continue
        r2_store.save_job(local)
        counts["exported"] += 1

    return counts


def sync_publish_results_from_r2(
    store,
    *,
    r2_store=None,
) -> dict:
    r2_store = r2_store or get_publish_job_store(mode="r2")
    counts = {"scanned": 0, "imported": 0, "skipped": 0}

    for remote_result in r2_store.list_results():
        counts["scanned"] += 1
        job_id = remote_result.get("job_id")
        if not job_id:
            counts["skipped"] += 1
            continue
        remote = deepcopy(remote_result)
        remote.setdefault("updated_at", _record_iso_updated_at(remote))
        local = store.load_result(job_id)
        if local is not None and _record_updated_at(remote) <= _record_updated_at(local):
            counts["skipped"] += 1
            continue
        store.save_result(job_id, remote)
        counts["imported"] += 1

    return counts


def sync_publish_results_to_r2(
    store,
    *,
    r2_store=None,
) -> dict:
    r2_store = r2_store or get_publish_job_store(mode="r2")
    counts = {"scanned": 0, "exported": 0, "skipped": 0}

    for local_result in store.list_results():
        counts["scanned"] += 1
        job_id = local_result.get("job_id")
        if not job_id:
            counts["skipped"] += 1
            continue
        local = deepcopy(local_result)
        local.setdefault("updated_at", _record_iso_updated_at(local))
        remote = r2_store.load_result(job_id)
        if remote is not None and _record_updated_at(local) <= _record_updated_at(remote):
            counts["skipped"] += 1
            continue
        r2_store.save_result(job_id, local)
        counts["exported"] += 1

    return counts


def sync_down(
    store,
    *,
    bucket: str | None = None,
    client=None,
    r2_publish_store=None,
) -> dict:
    return {
        "submissions": sync_submissions_from_r2(
            store, bucket=bucket, client=client,
        ),
        "publish_jobs": sync_publish_jobs_from_r2(
            store, r2_store=r2_publish_store,
        ),
        "publish_results": sync_publish_results_from_r2(
            store, r2_store=r2_publish_store,
        ),
    }


def sync_up(
    store,
    *,
    bucket: str | None = None,
    client=None,
    r2_publish_store=None,
) -> dict:
    return {
        "submissions": sync_submissions_to_r2(
            store, bucket=bucket, client=client,
        ),
        "publish_jobs": sync_publish_jobs_to_r2(
            store, r2_store=r2_publish_store,
        ),
        "publish_results": sync_publish_results_to_r2(
            store, r2_store=r2_publish_store,
        ),
    }


def _drift_counts(local_records: dict, remote_records: dict) -> dict:
    local_keys = set(local_records)
    remote_keys = set(remote_records)
    shared = local_keys & remote_keys

    newer_local = 0
    newer_remote = 0
    in_sync = 0
    for key in shared:
        local_ts = _record_updated_at(local_records[key])
        remote_ts = _record_updated_at(remote_records[key])
        if local_ts > remote_ts:
            newer_local += 1
        elif remote_ts > local_ts:
            newer_remote += 1
        else:
            in_sync += 1

    return {
        "local": len(local_keys),
        "remote": len(remote_keys),
        "only_local": len(local_keys - remote_keys),
        "only_remote": len(remote_keys - local_keys),
        "newer_local": newer_local,
        "newer_remote": newer_remote,
        "in_sync": in_sync,
    }


def queue_status(
    store,
    *,
    bucket: str | None = None,
    client=None,
    r2_publish_store=None,
    podcast_bucket: str | None = None,
    podcast_client=None,
) -> dict:
    """Build a health/status snapshot comparing local vs remote state.

    Returns a dict with per-section counts and drift info, plus a list
    of draft_generated submissions missing their playable MP3 in the
    podcast bucket.
    """
    bucket = bucket or DEFAULT_ADMIN_BUCKET
    client = client or get_r2_client()
    r2_publish_store = r2_publish_store or get_publish_job_store(mode="r2")
    podcast_bucket = podcast_bucket or os.environ.get(
        "BUCKET_NAME"
    ) or os.environ.get("PODCAST_BUCKET_NAME") or "ai-post-transformers"
    podcast_client = podcast_client or client

    # --- submissions ---
    remote_subs = {
        key: _read_submission(bucket=bucket, client=client, key=key)
        for key in _list_submission_keys(bucket=bucket, client=client)
    }
    local_subs = store.list_submissions()
    local_subs_by_key = {s["_key"]: s for s in local_subs}
    local_by_status = {}
    for s in local_subs:
        st = s.get("status", "unknown")
        local_by_status[st] = local_by_status.get(st, 0) + 1

    sub_status = _drift_counts(local_subs_by_key, remote_subs)
    sub_status["by_status"] = local_by_status

    # --- publish jobs ---
    r2_jobs = r2_publish_store.list_jobs()
    local_jobs = store.list_jobs()
    r2_jobs_by_id = {j["job_id"]: j for j in r2_jobs}
    local_jobs_by_id = {j["job_id"]: j for j in local_jobs}
    local_jobs_by_state = {}
    for j in local_jobs:
        st = j.get("state", "unknown")
        local_jobs_by_state[st] = local_jobs_by_state.get(st, 0) + 1

    jobs_status = _drift_counts(local_jobs_by_id, r2_jobs_by_id)
    jobs_status["by_state"] = local_jobs_by_state

    # --- publish results ---
    r2_results = r2_publish_store.list_results()
    local_results = store.list_results()
    r2_results_by_id = {
        r.get("job_id"): r for r in r2_results if r.get("job_id")
    }
    local_results_by_id = {
        r.get("job_id"): r for r in local_results if r.get("job_id")
    }
    results_status = _drift_counts(local_results_by_id, r2_results_by_id)

    # --- draft MP3 check ---
    draft_subs = [
        s for s in local_subs if s.get("status") == "draft_generated"
    ]
    missing_drafts = []
    for s in draft_subs:
        stem = s.get("draft_stem", "")
        if not stem:
            missing_drafts.append({
                "key": s["_key"],
                "reason": "no draft_stem recorded",
            })
            continue
        r2_key = f"{stem}.mp3"
        try:
            podcast_client.get_object(
                Bucket=podcast_bucket, Key=r2_key,
            )
        except Exception:
            missing_drafts.append({
                "key": s["_key"],
                "draft_stem": stem,
                "expected_r2_key": r2_key,
                "reason": "mp3 not found in podcast bucket",
            })

    return {
        "submissions": sub_status,
        "publish_jobs": jobs_status,
        "publish_results": results_status,
        "missing_draft_mp3": missing_drafts,
        "missing_draft_mp3_count": len(missing_drafts),
        "draft_generated_count": len(draft_subs),
    }


def _format_status_text(status: dict) -> str:
    """Format queue_status() output for human-readable CLI display."""
    lines = []
    lines.append("=== Queue Health Status ===")

    subs = status["submissions"]
    lines.append(f"\nSubmissions: {subs['local']} local, "
                 f"{subs['remote']} remote")
    if (subs["only_local"] or subs["only_remote"]
            or subs["newer_local"] or subs["newer_remote"]):
        lines.append(
            f"  drift: {subs['only_local']} only-local, "
            f"{subs['only_remote']} only-remote, "
            f"{subs['newer_local']} local-newer, "
            f"{subs['newer_remote']} remote-newer"
        )
    if subs.get("by_status"):
        parts = [f"{k}={v}" for k, v in sorted(subs["by_status"].items())]
        lines.append(f"  breakdown: {', '.join(parts)}")

    jobs = status["publish_jobs"]
    lines.append(f"\nPublish Jobs: {jobs['local']} local, "
                 f"{jobs['remote']} remote")
    if (jobs["only_local"] or jobs["only_remote"]
            or jobs["newer_local"] or jobs["newer_remote"]):
        lines.append(
            f"  drift: {jobs['only_local']} only-local, "
            f"{jobs['only_remote']} only-remote, "
            f"{jobs['newer_local']} local-newer, "
            f"{jobs['newer_remote']} remote-newer"
        )
    if jobs.get("by_state"):
        parts = [f"{k}={v}" for k, v in sorted(jobs["by_state"].items())]
        lines.append(f"  breakdown: {', '.join(parts)}")

    res = status["publish_results"]
    lines.append(f"\nPublish Results: {res['local']} local, "
                 f"{res['remote']} remote")
    if (res["only_local"] or res["only_remote"]
            or res["newer_local"] or res["newer_remote"]):
        lines.append(
            f"  drift: {res['only_local']} only-local, "
            f"{res['only_remote']} only-remote, "
            f"{res['newer_local']} local-newer, "
            f"{res['newer_remote']} remote-newer"
        )

    dg = status["draft_generated_count"]
    missing = status["missing_draft_mp3_count"]
    lines.append(f"\nDraft Playability: {dg} draft_generated, "
                 f"{missing} missing MP3")
    for m in status["missing_draft_mp3"]:
        lines.append(f"  - {m['key']}: {m['reason']}")

    return "\n".join(lines)


def _summary_string(direction: str, summary: dict) -> str:
    parts = []
    for section, counts in summary.items():
        changed = counts.get("imported", 0) + counts.get("exported", 0)
        parts.append(
            f"{section}: scanned={counts.get('scanned', 0)} "
            f"changed={changed} skipped={counts.get('skipped', 0)}"
        )
    return f"[queue-bridge] {direction} :: " + ", ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync queue state between R2 and a local SQLite queue DB.",
    )
    parser.add_argument(
        "mode",
        choices=("down", "up", "sync", "status"),
        help="down = R2 -> SQLite, up = SQLite -> R2, "
             "sync = down then up, status = health report",
    )
    parser.add_argument("--queue-db", required=True)
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output status as JSON instead of human-readable text",
    )
    args = parser.parse_args()

    store = get_queue_store(mode="sqlite", path=args.queue_db)

    if args.mode == "status":
        result = queue_status(store)
        if args.json_output:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(_format_status_text(result))
        return 0

    if args.mode == "down":
        summary = sync_down(store)
        print(_summary_string("down", summary))
        return 0
    if args.mode == "up":
        summary = sync_up(store)
        print(_summary_string("up", summary))
        return 0

    down = sync_down(store)
    print(_summary_string("down", down))
    up = sync_up(store)
    print(_summary_string("up", up))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
