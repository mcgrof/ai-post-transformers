"""Storage backends for durable publish jobs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from r2_upload import get_r2_client

from scripts.publish_jobs import (
    _atomic_write_json,
    job_path,
    jobs_dir,
    load_job as _load_job_local,
    result_path,
    results_dir,
    validate_job,
)


class LocalPublishJobStore:
    """Filesystem-backed publish job storage."""

    def __init__(self, root: Path | None = None):
        self.root = root

    def describe(self) -> str:
        base = self.root if self.root is not None else jobs_dir()
        return f"local:{base}"

    def load_job(self, job_or_path: str | Path) -> dict:
        return _load_job_local(job_or_path, root=self.root)

    def save_job(self, job: dict) -> str:
        validate_job(job)
        path = job_path(job, root=self.root)
        _atomic_write_json(path, job)
        return str(path)

    def save_result(self, job_id: str, result: dict) -> str:
        path = result_path(job_id, root=self.root)
        _atomic_write_json(path, result)
        return str(path)

    def load_result(self, job_id: str) -> dict | None:
        path = result_path(job_id, root=self.root)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def list_results(self) -> list[dict]:
        records = []
        for path in sorted(results_dir(self.root).glob("*.json")):
            data = json.loads(path.read_text())
            data.setdefault("job_id", path.stem)
            records.append(data)
        return records

    def list_jobs(self) -> list[dict]:
        records = []
        for path in sorted(jobs_dir(self.root).glob("*.json")):
            records.append(self.load_job(path))
        return records


class R2PublishJobStore:
    """Cloudflare R2-backed publish job storage."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        client=None,
        jobs_prefix: str = "publish-jobs/",
        results_prefix: str = "publish-results/",
    ):
        self.bucket = bucket or os.environ.get("ADMIN_BUCKET_NAME", "podcast-admin")
        self.client = client or get_r2_client()
        self.jobs_prefix = jobs_prefix
        self.results_prefix = results_prefix

    def describe(self) -> str:
        return f"r2:{self.bucket}"

    def _normalize_job_key(self, job_or_path: str | Path) -> str:
        raw = str(job_or_path)
        if raw.startswith(self.jobs_prefix):
            return raw
        candidate = Path(raw)
        if candidate.name.endswith(".json"):
            return f"{self.jobs_prefix}{candidate.name}"
        return f"{self.jobs_prefix}{raw}.json"

    def _normalize_result_key(self, job_or_id: str) -> str:
        raw = str(job_or_id)
        candidate = Path(raw)
        if raw.startswith(self.results_prefix):
            return raw
        if candidate.name.endswith(".json"):
            return f"{self.results_prefix}{candidate.name}"
        return f"{self.results_prefix}{raw}.json"

    def _read_json(self, key: str) -> dict:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        data = obj["Body"].read()
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    def _write_json(self, key: str, data: dict) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(data, indent=2, sort_keys=True) + "\n",
            ContentType="application/json",
        )
        return key

    def load_job(self, job_or_path: str | Path) -> dict:
        job = self._read_json(self._normalize_job_key(job_or_path))
        validate_job(job)
        return job

    def save_job(self, job: dict) -> str:
        validate_job(job)
        return self._write_json(self._normalize_job_key(job["job_id"]), job)

    def save_result(self, job_id: str, result: dict) -> str:
        return self._write_json(self._normalize_result_key(job_id), result)

    def load_result(self, job_id: str) -> dict | None:
        key = self._normalize_result_key(job_id)
        try:
            return self._read_json(key)
        except Exception as exc:
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                return None
            raise

    def list_results(self) -> list[dict]:
        paginator = self.client.get_paginator("list_objects_v2")
        records = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.results_prefix):
            for obj in sorted(page.get("Contents", []), key=lambda item: item["Key"]):
                key = obj["Key"]
                data = self._read_json(key)
                # Derive job_id from key if not embedded
                stem = key[len(self.results_prefix):]
                if stem.endswith(".json"):
                    stem = stem[:-5]
                data.setdefault("job_id", stem)
                records.append(data)
        return records

    def list_jobs(self) -> list[dict]:
        paginator = self.client.get_paginator("list_objects_v2")
        records = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.jobs_prefix):
            for obj in sorted(page.get("Contents", []), key=lambda item: item["Key"]):
                records.append(self.load_job(obj["Key"]))
        return records


def can_use_r2_store() -> bool:
    required = ("AWS_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    return all(os.environ.get(name) for name in required)


def get_publish_job_store(
    *,
    mode: str = "auto",
    root: Path | None = None,
    client=None,
    queue_db: str | Path | None = None,
):
    if mode not in {"auto", "local", "r2", "sqlite"}:
        raise ValueError(f"unknown publish job store mode: {mode}")
    if mode == "sqlite" or (mode == "auto" and queue_db):
        from scripts.queue_store import SQLiteQueueStore
        if not queue_db:
            raise ValueError("sqlite mode requires queue_db path")
        return SQLiteQueueStore(queue_db)
    if mode == "local" or root is not None:
        return LocalPublishJobStore(root=root)
    if mode == "r2":
        return R2PublishJobStore(client=client)
    if can_use_r2_store():
        return R2PublishJobStore(client=client)
    return LocalPublishJobStore(root=root)
