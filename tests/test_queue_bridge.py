"""Tests for R2 <-> SQLite queue bridge.

Uses a fake R2 client (in-memory dict) and a fake R2PublishJobStore to
validate import/export logic, timestamp-based conflict resolution, and
the combined sync cycle.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from scripts.queue_bridge import (
    sync_submissions_from_r2,
    sync_submissions_to_r2,
    sync_publish_jobs_from_r2,
    sync_publish_jobs_to_r2,
    sync_down,
    sync_up,
    _summary_string,
)
from scripts.queue_store import SQLiteQueueStore
from scripts.publish_jobs import make_job_record


# ---------------------------------------------------------------------------
# Fake R2 client (for submissions)
# ---------------------------------------------------------------------------


class FakeR2Client:
    """Minimal S3-compatible fake backed by a dict."""

    def __init__(self):
        self._objects: dict[str, dict[str, bytes]] = {}

    def _ensure_bucket(self, bucket):
        self._objects.setdefault(bucket, {})

    def put_object(self, *, Bucket, Key, Body, ContentType="application/json"):
        self._ensure_bucket(Bucket)
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self._objects[Bucket][Key] = Body

    def get_object(self, *, Bucket, Key):
        self._ensure_bucket(Bucket)
        data = self._objects[Bucket].get(Key)
        if data is None:
            raise Exception(f"NoSuchKey: {Key}")
        return {"Body": BytesIO(data)}

    def get_paginator(self, method):
        assert method == "list_objects_v2"
        return _FakePaginator(self)


class _FakePaginator:
    def __init__(self, client: FakeR2Client):
        self._client = client

    def paginate(self, *, Bucket, Prefix):
        self._client._ensure_bucket(Bucket)
        contents = []
        for key in sorted(self._client._objects[Bucket]):
            if key.startswith(Prefix):
                contents.append({"Key": key})
        yield {"Contents": contents}


# ---------------------------------------------------------------------------
# Fake R2 publish job store (for publish jobs)
# ---------------------------------------------------------------------------


class FakeR2PublishJobStore:
    """In-memory store mimicking R2PublishJobStore interface."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}

    def list_jobs(self) -> list[dict]:
        from scripts.publish_jobs import validate_job
        results = []
        for job_id in sorted(self._jobs):
            job = deepcopy(self._jobs[job_id])
            validate_job(job)
            results.append(job)
        return results

    def load_job(self, job_or_path) -> dict:
        from scripts.publish_jobs import validate_job
        raw = str(job_or_path)
        if raw.startswith("publish-jobs/"):
            raw = raw[len("publish-jobs/"):]
        if raw.endswith(".json"):
            raw = raw[:-5]
        if raw not in self._jobs:
            raise KeyError(f"publish job not found: {raw}")
        job = deepcopy(self._jobs[raw])
        validate_job(job)
        return job

    def save_job(self, job) -> str:
        from scripts.publish_jobs import validate_job
        validate_job(job)
        self._jobs[job["job_id"]] = deepcopy(job)
        return job["job_id"]


def _put_json(client, bucket, key, data):
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, sort_keys=True),
    )


def _get_json(client, bucket, key):
    obj = client.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


BUCKET = "podcast-admin"


@pytest.fixture
def store(tmp_path):
    return SQLiteQueueStore(tmp_path / "queue.db")


@pytest.fixture
def client():
    return FakeR2Client()


@pytest.fixture
def r2_pub_store():
    return FakeR2PublishJobStore()


def _ts(minutes_ago=0):
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Import submissions from R2
# ---------------------------------------------------------------------------


class TestSyncSubmissionsFromR2:
    def test_imports_new_submission(self, store, client):
        sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(5),
        }
        _put_json(client, BUCKET, "submissions/test.json", sub)

        counts = sync_submissions_from_r2(
            store, bucket=BUCKET, client=client
        )
        assert counts["imported"] == 1
        assert counts["scanned"] == 1

        result = store.load_submission("submissions/test.json")
        assert result is not None
        data, _ver = result
        assert data["status"] == "submitted"

    def test_skips_when_local_is_newer(self, store, client):
        local_sub = {
            "status": "generation_running",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(0),
        }
        store.save_submission("submissions/test.json", local_sub)

        r2_sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(10),
        }
        _put_json(client, BUCKET, "submissions/test.json", r2_sub)

        counts = sync_submissions_from_r2(
            store, bucket=BUCKET, client=client
        )
        assert counts["imported"] == 0
        assert counts["skipped"] == 1

        data, _ = store.load_submission("submissions/test.json")
        assert data["status"] == "generation_running"

    def test_updates_when_r2_is_newer(self, store, client):
        local_sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(10),
        }
        store.save_submission("submissions/test.json", local_sub)

        r2_sub = {
            "status": "generation_failed",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(0),
        }
        _put_json(client, BUCKET, "submissions/test.json", r2_sub)

        counts = sync_submissions_from_r2(
            store, bucket=BUCKET, client=client
        )
        assert counts["imported"] == 1

        data, _ = store.load_submission("submissions/test.json")
        assert data["status"] == "generation_failed"


# ---------------------------------------------------------------------------
# Import publish jobs from R2
# ---------------------------------------------------------------------------


class TestSyncPublishJobsFromR2:
    def test_imports_new_job(self, store, r2_pub_store):
        job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            title="Example",
            job_id="pub_2026_03_29_100000",
        )
        job["updated_at"] = _ts(5)
        r2_pub_store.save_job(job)

        counts = sync_publish_jobs_from_r2(
            store, r2_store=r2_pub_store
        )
        assert counts["imported"] == 1

        loaded = store.load_job("pub_2026_03_29_100000")
        assert loaded["title"] == "Example"

    def test_skips_when_local_is_newer(self, store, r2_pub_store):
        job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            job_id="pub_2026_03_29_100000",
        )
        job["updated_at"] = _ts(0)
        store.save_job(job)

        r2_job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            job_id="pub_2026_03_29_100000",
        )
        r2_job["updated_at"] = _ts(10)
        r2_pub_store.save_job(r2_job)

        counts = sync_publish_jobs_from_r2(
            store, r2_store=r2_pub_store
        )
        assert counts["imported"] == 0
        assert counts["skipped"] == 1


# ---------------------------------------------------------------------------
# Export submissions to R2
# ---------------------------------------------------------------------------


class TestSyncSubmissionsToR2:
    def test_exports_new_submission(self, store, client):
        sub = {
            "status": "draft_generated",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(0),
        }
        store.save_submission("submissions/test.json", sub)
        client._ensure_bucket(BUCKET)

        counts = sync_submissions_to_r2(
            store, bucket=BUCKET, client=client
        )
        assert counts["exported"] == 1

        r2_data = _get_json(client, BUCKET, "submissions/test.json")
        assert r2_data["status"] == "draft_generated"

    def test_skips_when_r2_is_newer(self, store, client):
        local_sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(10),
        }
        store.save_submission("submissions/test.json", local_sub)

        r2_sub = {
            "status": "generation_running",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(0),
        }
        _put_json(client, BUCKET, "submissions/test.json", r2_sub)

        counts = sync_submissions_to_r2(
            store, bucket=BUCKET, client=client
        )
        assert counts["exported"] == 0
        assert counts["skipped"] == 1

        r2_data = _get_json(client, BUCKET, "submissions/test.json")
        assert r2_data["status"] == "generation_running"

    def test_exports_when_local_is_newer(self, store, client):
        local_sub = {
            "status": "draft_generated",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(0),
        }
        store.save_submission("submissions/test.json", local_sub)

        r2_sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(10),
        }
        _put_json(client, BUCKET, "submissions/test.json", r2_sub)

        counts = sync_submissions_to_r2(
            store, bucket=BUCKET, client=client
        )
        assert counts["exported"] == 1

        r2_data = _get_json(client, BUCKET, "submissions/test.json")
        assert r2_data["status"] == "draft_generated"


# ---------------------------------------------------------------------------
# Export publish jobs to R2
# ---------------------------------------------------------------------------


class TestSyncPublishJobsToR2:
    def test_exports_new_job(self, store, r2_pub_store):
        job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            job_id="pub_2026_03_29_120000",
        )
        job["updated_at"] = _ts(0)
        store.save_job(job)

        counts = sync_publish_jobs_to_r2(
            store, r2_store=r2_pub_store
        )
        assert counts["exported"] == 1

        loaded = r2_pub_store.load_job("pub_2026_03_29_120000")
        assert loaded["job_id"] == "pub_2026_03_29_120000"

    def test_skips_when_r2_is_newer(self, store, r2_pub_store):
        job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            job_id="pub_2026_03_29_120000",
        )
        job["updated_at"] = _ts(10)
        store.save_job(job)

        r2_job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            job_id="pub_2026_03_29_120000",
        )
        r2_job["updated_at"] = _ts(0)
        r2_pub_store.save_job(r2_job)

        counts = sync_publish_jobs_to_r2(
            store, r2_store=r2_pub_store
        )
        assert counts["exported"] == 0
        assert counts["skipped"] == 1


# ---------------------------------------------------------------------------
# Full sync cycle
# ---------------------------------------------------------------------------


class TestSyncCycle:
    def test_roundtrip(self, store, client, r2_pub_store):
        """R2 submission imported, then local change exported back."""
        r2_sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(5),
        }
        _put_json(client, BUCKET, "submissions/round.json", r2_sub)

        result = sync_down(
            store, bucket=BUCKET, client=client,
            r2_publish_store=r2_pub_store,
        )
        assert result["submissions"]["imported"] == 1

        store.update_submission("submissions/round.json", {
            "status": "generation_running",
        })

        result = sync_up(
            store, bucket=BUCKET, client=client,
            r2_publish_store=r2_pub_store,
        )
        assert result["submissions"]["exported"] == 1

        r2_data = _get_json(client, BUCKET, "submissions/round.json")
        assert r2_data["status"] == "generation_running"

    def test_no_clobber_newer_remote(self, store, client, r2_pub_store):
        """Local stale record does not overwrite newer R2 record."""
        old_sub = {
            "status": "submitted",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(20),
        }
        store.save_submission("submissions/stale.json", old_sub)

        new_r2 = {
            "status": "draft_generated",
            "urls": ["https://arxiv.org/pdf/1234.56789"],
            "updated_at": _ts(0),
        }
        _put_json(client, BUCKET, "submissions/stale.json", new_r2)

        down = sync_down(
            store, bucket=BUCKET, client=client,
            r2_publish_store=r2_pub_store,
        )
        assert down["submissions"]["imported"] == 1

        up = sync_up(
            store, bucket=BUCKET, client=client,
            r2_publish_store=r2_pub_store,
        )
        assert up["submissions"]["exported"] == 0

        data, _ = store.load_submission("submissions/stale.json")
        assert data["status"] == "draft_generated"


# ---------------------------------------------------------------------------
# Summary string
# ---------------------------------------------------------------------------


class TestSummaryString:
    def test_format(self):
        summary = {
            "submissions": {"scanned": 5, "imported": 2, "skipped": 3},
            "publish_jobs": {"scanned": 1, "imported": 1, "skipped": 0},
        }
        s = _summary_string("down", summary)
        assert "[queue-bridge] down ::" in s
        assert "submissions:" in s
        assert "publish_jobs:" in s


# ---------------------------------------------------------------------------
# Combined worker bridge orchestration
# ---------------------------------------------------------------------------


class TestWorkerBridgeOrchestration:
    def test_run_once_with_queue_db_calls_bridge(
        self, store, monkeypatch
    ):
        """Verify the combined worker imports before and exports after."""
        import_calls = []
        export_calls = []

        def fake_import(s):
            import_calls.append(1)

        def fake_export(s):
            export_calls.append(1)

        monkeypatch.setattr(
            "scripts.run_podcast_worker._bridge_import",
            fake_import,
        )
        monkeypatch.setattr(
            "scripts.run_podcast_worker._bridge_export",
            fake_export,
        )

        from scripts.run_podcast_worker import run_once

        class DummyStore:
            def list_jobs(self):
                return []

        run_once(
            "test-admin", None, 900, False,
            store=DummyStore(),
            queue_db_store=store,
        )

        assert len(import_calls) == 1
        assert len(export_calls) == 1

    def test_run_once_without_queue_db_skips_bridge(self, monkeypatch):
        """Without queue_db_store, bridge functions are not called."""
        import_calls = []

        def fake_import(s):
            import_calls.append(1)

        monkeypatch.setattr(
            "scripts.run_podcast_worker._bridge_import",
            fake_import,
        )

        from scripts.run_podcast_worker import run_once

        class DummyStore:
            def list_jobs(self):
                return []

        run_once(
            "test-admin", None, 900, False,
            store=DummyStore(),
        )

        assert len(import_calls) == 0
