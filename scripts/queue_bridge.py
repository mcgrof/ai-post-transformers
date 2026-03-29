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
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

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
    }


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
        choices=("down", "up", "sync"),
        help="down = R2 -> SQLite, up = SQLite -> R2, sync = down then up",
    )
    parser.add_argument("--queue-db", required=True)
    args = parser.parse_args()

    store = get_queue_store(mode="sqlite", path=args.queue_db)
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
