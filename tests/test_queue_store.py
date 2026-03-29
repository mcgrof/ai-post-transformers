"""Tests for SQLiteQueueStore and InMemoryQueueStore.

Validates transactional semantics, CAS, leases, claim tokens,
heartbeats, stale release, and history for both implementations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from scripts.queue_store import (
    CASConflictError,
    InMemoryQueueStore,
    SQLiteQueueStore,
    get_queue_store,
)
from scripts.publish_jobs import claim_job, make_job_record, save_job, load_job


def _utcnow():
    return datetime.now(timezone.utc)


def _make_sub(status="submitted", urls=None, admin_id=None,
              claim_token=None, lease_expires_at=None):
    sub = {
        "status": status,
        "urls": urls or ["https://arxiv.org/pdf/1234.56789"],
        "timestamp": "2026-03-28T10:00:00+00:00",
        "status_history": [],
    }
    if admin_id:
        sub["claimed_by"] = admin_id
    if claim_token:
        sub["claim_token"] = claim_token
    if lease_expires_at:
        sub["lease_expires_at"] = lease_expires_at
    return sub


# -----------------------------------------------------------------------
# Parametrize over both store implementations
# -----------------------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryQueueStore()
    return SQLiteQueueStore(tmp_path / "queue.db")


# -----------------------------------------------------------------------
# Submission basics
# -----------------------------------------------------------------------


class TestSubmissionCRUD:
    def test_save_and_load(self, store):
        sub = _make_sub()
        version = store.save_submission("submissions/test.json", sub)
        assert version == 1

        result = store.load_submission("submissions/test.json")
        assert result is not None
        data, ver = result
        assert ver == 1
        assert data["status"] == "submitted"

    def test_update_increments_version(self, store):
        sub = _make_sub()
        store.save_submission("submissions/test.json", sub)

        sub["status"] = "generation_running"
        v2 = store.save_submission("submissions/test.json", sub)
        assert v2 == 2

        result = store.load_submission("submissions/test.json")
        assert result[1] == 2

    def test_cas_rejects_stale_version(self, store):
        sub = _make_sub()
        store.save_submission("submissions/test.json", sub)
        sub["status"] = "generation_running"
        store.save_submission("submissions/test.json", sub)

        with pytest.raises(CASConflictError):
            store.save_submission(
                "submissions/test.json", sub, expected_version=1
            )

    def test_cas_accepts_correct_version(self, store):
        sub = _make_sub()
        store.save_submission("submissions/test.json", sub)

        sub["status"] = "generation_running"
        v2 = store.save_submission(
            "submissions/test.json", sub, expected_version=1
        )
        assert v2 == 2

    def test_load_missing_returns_none(self, store):
        assert store.load_submission("submissions/nope.json") is None

    def test_list_submissions(self, store):
        store.save_submission("submissions/a.json", _make_sub())
        store.save_submission("submissions/b.json",
                              _make_sub(status="generation_running"))

        all_subs = store.list_submissions()
        assert len(all_subs) == 2

        submitted_only = store.list_submissions(status="submitted")
        assert len(submitted_only) == 1
        assert submitted_only[0]["_key"] == "submissions/a.json"

    def test_update_submission(self, store):
        store.save_submission("submissions/test.json", _make_sub())
        updated = store.update_submission(
            "submissions/test.json",
            {"status": "generation_running", "claimed_by": "worker-1"},
        )
        assert updated["status"] == "generation_running"
        assert updated["claimed_by"] == "worker-1"
        assert updated["_key"] == "submissions/test.json"

    def test_update_missing_raises(self, store):
        with pytest.raises(KeyError):
            store.update_submission("submissions/nope.json", {"status": "x"})


# -----------------------------------------------------------------------
# Claim semantics
# -----------------------------------------------------------------------


class TestClaimSubmission:
    def test_claim_sets_token_and_lease(self, store):
        store.save_submission("submissions/test.json", _make_sub())
        claimed = store.claim_submission(
            "submissions/test.json", "admin-1"
        )

        assert claimed["status"] == "generation_claimed"
        assert claimed["claimed_by"] == "admin-1"
        assert claimed["claim_token"] is not None
        uuid.UUID(claimed["claim_token"])
        assert claimed["lease_expires_at"] is not None

    def test_claim_missing_returns_none(self, store):
        result = store.claim_submission("submissions/nope.json", "admin-1")
        assert result is None

    def test_claim_persists(self, store):
        store.save_submission("submissions/test.json", _make_sub())
        store.claim_submission("submissions/test.json", "admin-1")

        data, _ = store.load_submission("submissions/test.json")
        assert data["status"] == "generation_claimed"
        assert data["claimed_by"] == "admin-1"


# -----------------------------------------------------------------------
# Heartbeat
# -----------------------------------------------------------------------


class TestHeartbeat:
    def test_heartbeat_extends_lease(self, store):
        store.save_submission("submissions/test.json", _make_sub())
        claimed = store.claim_submission(
            "submissions/test.json", "admin-1"
        )
        token = claimed["claim_token"]

        result = store.heartbeat_submission(
            "submissions/test.json", token
        )
        assert result is not None

        data, _ = store.load_submission("submissions/test.json")
        expires = datetime.fromisoformat(data["lease_expires_at"])
        assert expires > _utcnow() + timedelta(seconds=1700)

    def test_heartbeat_rejects_wrong_token(self, store):
        store.save_submission("submissions/test.json", _make_sub())
        store.claim_submission("submissions/test.json", "admin-1")

        result = store.heartbeat_submission(
            "submissions/test.json", "wrong-token"
        )
        assert result is None


# -----------------------------------------------------------------------
# Verify claim token
# -----------------------------------------------------------------------


class TestVerifyClaimToken:
    def test_matching_token(self, store):
        store.save_submission("submissions/test.json",
                              _make_sub(claim_token="my-token"))
        assert store.verify_claim_token(
            "submissions/test.json", "my-token"
        ) is True

    def test_mismatched_token(self, store):
        store.save_submission("submissions/test.json",
                              _make_sub(claim_token="my-token"))
        assert store.verify_claim_token(
            "submissions/test.json", "other"
        ) is False

    def test_missing_key(self, store):
        assert store.verify_claim_token("nope", "token") is False


# -----------------------------------------------------------------------
# Stale release
# -----------------------------------------------------------------------


class TestStaleRelease:
    def test_releases_expired_claim(self, store):
        sub = _make_sub(
            status="generation_claimed",
            admin_id="admin-1",
            claim_token="tok-1",
            lease_expires_at="2000-01-01T00:00:00+00:00",
        )
        store.save_submission("submissions/test.json", sub)

        released = store.release_stale_submissions()
        assert released == 1

        data, _ = store.load_submission("submissions/test.json")
        assert data["status"] == "submitted"
        assert data.get("claim_token") is None

    def test_leaves_active_lease(self, store):
        future = (_utcnow() + timedelta(hours=1)).isoformat()
        sub = _make_sub(
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok-2",
            lease_expires_at=future,
        )
        store.save_submission("submissions/test.json", sub)

        released = store.release_stale_submissions()
        assert released == 0

    def test_ignores_records_without_token(self, store):
        sub = _make_sub(
            status="generation_claimed",
            admin_id="admin-1",
        )
        store.save_submission("submissions/test.json", sub)
        assert store.release_stale_submissions() == 0


# -----------------------------------------------------------------------
# Active / pending queries
# -----------------------------------------------------------------------


class TestActiveAndPending:
    def test_active_for_admin(self, store):
        future = (_utcnow() + timedelta(hours=1)).isoformat()
        store.save_submission("submissions/active.json", _make_sub(
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok",
            lease_expires_at=future,
        ))
        store.save_submission("submissions/other.json", _make_sub(
            status="generation_running",
            admin_id="admin-2",
            claim_token="tok2",
            lease_expires_at=future,
        ))

        active = store.active_submissions_for_admin("admin-1")
        assert len(active) == 1
        assert active[0]["_key"] == "submissions/active.json"

    def test_expired_not_counted_as_active(self, store):
        store.save_submission("submissions/stale.json", _make_sub(
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok",
            lease_expires_at="2000-01-01T00:00:00+00:00",
        ))
        assert len(store.active_submissions_for_admin("admin-1")) == 0

    def test_find_pending(self, store):
        store.save_submission("submissions/a.json", _make_sub())
        store.save_submission("submissions/b.json",
                              _make_sub(status="generation_running"))
        store.save_submission("submissions/c.json",
                              _make_sub(status="pending"))

        pending = store.find_pending_submissions()
        statuses = {s["status"] for s in pending}
        assert statuses == {"submitted", "pending"}


# -----------------------------------------------------------------------
# Publish jobs
# -----------------------------------------------------------------------


class TestPublishJobs:
    def test_save_and_load_job(self, store):
        job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
            title="Example",
            episode_id=42,
        )
        store.save_job(job)
        loaded = store.load_job(job["job_id"])
        assert loaded["title"] == "Example"
        assert loaded["state"] == "approved_for_publish"

    def test_list_jobs(self, store):
        job1 = make_job_record(
            draft_key="drafts/2026/03/a.mp3",
            job_id="pub_2026_03_28_100000",
            created_at="2026-03-28T10:00:00+00:00",
        )
        job2 = make_job_record(
            draft_key="drafts/2026/03/b.mp3",
            job_id="pub_2026_03_28_110000",
            created_at="2026-03-28T11:00:00+00:00",
        )
        store.save_job(job1)
        store.save_job(job2)

        jobs = store.list_jobs()
        assert len(jobs) == 2

    def test_claim_and_save(self, store):
        job = make_job_record(
            draft_key="drafts/2026/03/example.mp3",
        )
        store.save_job(job)
        loaded = store.load_job(job["job_id"])
        claim_job(loaded, admin_id="admin-1", admin_name="mcgrof")
        save_job(loaded, store=store)

        reloaded = store.load_job(job["job_id"])
        assert reloaded["state"] == "publish_claimed"
        assert reloaded["claimed_by_admin_id"] == "admin-1"

    def test_save_result(self, store):
        store.save_result("pub_test", {"state": "publish_completed"})
        # No error means success

    def test_load_missing_job_raises(self, store):
        with pytest.raises(KeyError):
            store.load_job("pub_nonexistent")


# -----------------------------------------------------------------------
# History
# -----------------------------------------------------------------------


class TestHistory:
    def test_submission_history_recorded(self, store):
        store.save_submission("submissions/test.json", _make_sub())
        store.update_submission(
            "submissions/test.json", {"status": "generation_running"}
        )

        history = store.get_history("submissions", "submissions/test.json")
        assert len(history) >= 2
        actions = [h["action"] for h in history]
        assert "created" in actions

    def test_publish_job_history(self, store):
        job = make_job_record(draft_key="drafts/2026/03/ex.mp3")
        store.save_job(job)
        job["state"] = "publish_claimed"
        job["claimed_by_admin_id"] = "admin-1"
        job["claimed_at"] = "2026-03-28T10:00:00+00:00"
        job["lease_expires_at"] = "2026-03-28T10:15:00+00:00"
        job["last_heartbeat_at"] = "2026-03-28T10:00:00+00:00"
        store.save_job(job)

        history = store.get_history("publish_jobs", job["job_id"])
        assert len(history) == 2


# -----------------------------------------------------------------------
# Factory
# -----------------------------------------------------------------------


class TestFactory:
    def test_memory_mode(self):
        store = get_queue_store(mode="memory")
        assert isinstance(store, InMemoryQueueStore)

    def test_sqlite_mode(self, tmp_path):
        store = get_queue_store(
            mode="sqlite", path=tmp_path / "test.db"
        )
        assert isinstance(store, SQLiteQueueStore)

    def test_auto_with_path(self, tmp_path):
        store = get_queue_store(path=tmp_path / "test.db")
        assert isinstance(store, SQLiteQueueStore)

    def test_auto_without_path(self):
        store = get_queue_store()
        assert isinstance(store, InMemoryQueueStore)
