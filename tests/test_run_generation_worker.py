"""Tests for generation worker hardening.

Validates lease semantics, stale-claim recovery, duplicate-run
protection, claim-token verification, and heartbeat behaviour
using an in-memory mock R2 backend.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.run_generation_worker import (
    GENERATION_ACTIVE_STATES,
    GENERATION_LEASE_SECONDS,
    _active_generation_for_admin,
    _claim_submission,
    _find_pending,
    _heartbeat_submission,
    _lease_is_active,
    _list_submissions,
    _read_submission,
    _release_stale_claims,
    _update_submission,
    _verify_claim_token,
    process_submission,
    run_once,
)


# ---------------------------------------------------------------------------
# In-memory R2 mock
# ---------------------------------------------------------------------------


class FakeR2:
    """Minimal in-memory S3/R2 mock supporting get/put/list/paginate."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType="application/json"):
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        data = self.objects.get(Key)
        if data is None:
            raise Exception(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(data)}

    def get_paginator(self, method):
        assert method == "list_objects_v2"
        return _FakePaginator(self)


class _FakePaginator:
    def __init__(self, store: FakeR2):
        self._store = store

    def paginate(self, *, Bucket, Prefix):
        contents = []
        for key in sorted(self._store.objects):
            if key.startswith(Prefix):
                contents.append({"Key": key})
        yield {"Contents": contents}


BUCKET = "podcast-admin"


def _make_submission(
    client: FakeR2,
    key: str = "submissions/test-sub.json",
    status: str = "submitted",
    admin_id: str | None = None,
    claim_token: str | None = None,
    lease_expires_at: str | None = None,
    last_heartbeat_at: str | None = None,
    urls: list[str] | None = None,
):
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
    if last_heartbeat_at:
        sub["last_heartbeat_at"] = last_heartbeat_at
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(sub, indent=2) + "\n",
    )
    return sub


# ---------------------------------------------------------------------------
# Lease helpers
# ---------------------------------------------------------------------------


class TestLeaseIsActive:
    def test_no_lease(self):
        assert _lease_is_active({}) is False

    def test_expired_lease(self):
        sub = {"lease_expires_at": "2000-01-01T00:00:00+00:00"}
        assert _lease_is_active(sub) is False

    def test_active_lease(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        sub = {"lease_expires_at": future}
        assert _lease_is_active(sub) is True


# ---------------------------------------------------------------------------
# Claim semantics
# ---------------------------------------------------------------------------


class TestClaimSubmission:
    def test_claim_sets_token_and_lease(self):
        client = FakeR2()
        _make_submission(client)
        sub = {"_key": "submissions/test-sub.json"}

        claimed = _claim_submission(BUCKET, client, sub, "admin-1")

        assert claimed["status"] == "generation_claimed"
        assert claimed["claimed_by"] == "admin-1"
        assert claimed["claim_token"] is not None
        uuid.UUID(claimed["claim_token"])  # valid uuid
        assert claimed["lease_expires_at"] is not None
        assert _lease_is_active(claimed) is True

    def test_claim_extends_lease_by_configured_seconds(self):
        client = FakeR2()
        _make_submission(client)
        sub = {"_key": "submissions/test-sub.json"}
        before = datetime.now(timezone.utc)

        claimed = _claim_submission(BUCKET, client, sub, "admin-1")

        expires = datetime.fromisoformat(claimed["lease_expires_at"])
        assert expires >= before + timedelta(seconds=GENERATION_LEASE_SECONDS - 5)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_heartbeat_extends_lease(self):
        client = FakeR2()
        token = str(uuid.uuid4())
        _make_submission(
            client,
            status="generation_running",
            admin_id="admin-1",
            claim_token=token,
            lease_expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=60)
            ).isoformat(),
        )

        result = _heartbeat_submission(
            BUCKET, client, "submissions/test-sub.json", token
        )

        assert result is not None
        refreshed = _read_submission(BUCKET, client, "submissions/test-sub.json")
        new_exp = datetime.fromisoformat(refreshed["lease_expires_at"])
        assert new_exp > datetime.now(timezone.utc) + timedelta(seconds=GENERATION_LEASE_SECONDS - 30)

    def test_heartbeat_rejects_wrong_token(self):
        client = FakeR2()
        _make_submission(
            client,
            status="generation_running",
            admin_id="admin-1",
            claim_token="original-token",
            lease_expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=60)
            ).isoformat(),
        )

        result = _heartbeat_submission(
            BUCKET, client, "submissions/test-sub.json", "wrong-token"
        )
        assert result is None


# ---------------------------------------------------------------------------
# Stale release
# ---------------------------------------------------------------------------


class TestReleaseStale:
    def test_releases_expired_claim(self):
        client = FakeR2()
        _make_submission(
            client,
            status="generation_claimed",
            admin_id="admin-1",
            claim_token="tok-1",
            lease_expires_at="2000-01-01T00:00:00+00:00",
        )

        released = _release_stale_claims(BUCKET, client)
        assert released == 1

        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert sub["status"] == "submitted"
        assert sub.get("claim_token") is None
        assert sub.get("lease_expires_at") is None

    def test_leaves_active_lease_alone(self):
        client = FakeR2()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _make_submission(
            client,
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok-2",
            lease_expires_at=future,
        )

        released = _release_stale_claims(BUCKET, client)
        assert released == 0

        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert sub["status"] == "generation_running"

    def test_ignores_legacy_records_without_lease(self):
        """Submissions created before lease support are not released."""
        client = FakeR2()
        _make_submission(
            client,
            status="generation_claimed",
            admin_id="admin-1",
        )

        released = _release_stale_claims(BUCKET, client)
        assert released == 0


# ---------------------------------------------------------------------------
# Duplicate-run protection
# ---------------------------------------------------------------------------


class TestDuplicateProtection:
    def test_active_generation_blocks_new_claim(self):
        client = FakeR2()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _make_submission(
            client,
            key="submissions/running.json",
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok-r",
            lease_expires_at=future,
        )

        active = _active_generation_for_admin(BUCKET, client, "admin-1")
        assert len(active) == 1

    def test_expired_generation_does_not_block(self):
        client = FakeR2()
        _make_submission(
            client,
            key="submissions/expired.json",
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok-e",
            lease_expires_at="2000-01-01T00:00:00+00:00",
        )

        active = _active_generation_for_admin(BUCKET, client, "admin-1")
        assert len(active) == 0

    def test_other_admin_does_not_block(self):
        client = FakeR2()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _make_submission(
            client,
            key="submissions/other.json",
            status="generation_running",
            admin_id="admin-2",
            claim_token="tok-o",
            lease_expires_at=future,
        )

        active = _active_generation_for_admin(BUCKET, client, "admin-1")
        assert len(active) == 0


# ---------------------------------------------------------------------------
# Claim-token verification
# ---------------------------------------------------------------------------


class TestVerifyClaimToken:
    def test_matching_token(self):
        client = FakeR2()
        _make_submission(client, claim_token="my-token")

        assert _verify_claim_token(
            BUCKET, client, "submissions/test-sub.json", "my-token"
        ) is True

    def test_mismatched_token(self):
        client = FakeR2()
        _make_submission(client, claim_token="my-token")

        assert _verify_claim_token(
            BUCKET, client, "submissions/test-sub.json", "other-token"
        ) is False


# ---------------------------------------------------------------------------
# run_once integration
# ---------------------------------------------------------------------------


class TestRunOnce:
    def test_skips_when_admin_has_active_generation(self):
        client = FakeR2()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _make_submission(
            client,
            key="submissions/active.json",
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok-a",
            lease_expires_at=future,
        )
        _make_submission(
            client,
            key="submissions/pending.json",
            status="submitted",
        )

        count = run_once("admin-1", bucket=BUCKET, client=client)
        assert count == 0

        sub = _read_submission(BUCKET, client, "submissions/pending.json")
        assert sub["status"] == "submitted"

    def test_releases_stale_then_processes_pending(self):
        """After releasing a stale claim, the oldest pending submission
        is picked up.  The formerly-stale record is now 'submitted' so
        it competes with the other pending record by timestamp."""
        client = FakeR2()
        _make_submission(
            client,
            key="submissions/stale.json",
            status="generation_running",
            admin_id="admin-1",
            claim_token="tok-stale",
            lease_expires_at="2000-01-01T00:00:00+00:00",
        )
        _make_submission(
            client,
            key="submissions/todo.json",
            status="submitted",
        )

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, "drafts/2026/03/test-draft"),
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(True, {"draft_artifacts": {"mp3": "https://podcast/drafts/2026/03/test-draft.mp3"}}),
        ):
            count = run_once("admin-1", bucket=BUCKET, client=client)

        assert count == 1

        # The stale record was released back to 'submitted', then it or
        # the other pending record was claimed and processed.  At least
        # one must now be draft_generated.
        stale = _read_submission(BUCKET, client, "submissions/stale.json")
        todo = _read_submission(BUCKET, client, "submissions/todo.json")
        generated = [
            s for s in (stale, todo)
            if s["status"] == "draft_generated"
        ]
        assert len(generated) == 1

        # The stale record must have had its lease cleared during
        # release (even if it was later re-claimed).
        stale_history = stale.get("status_history", [])
        assert any(
            entry.get("status") == "submitted" for entry in stale_history
        )

    def test_no_pending_returns_zero(self):
        client = FakeR2()
        count = run_once("admin-1", bucket=BUCKET, client=client)
        assert count == 0


# ---------------------------------------------------------------------------
# process_submission with token verification
# ---------------------------------------------------------------------------


class TestProcessSubmission:
    def test_success_path_writes_draft_generated(self):
        client = FakeR2()
        _make_submission(client)
        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, "drafts/2026/03/good-draft"),
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(True, {"draft_artifacts": {"mp3": "https://podcast/drafts/2026/03/good-draft.mp3"}}),
        ):
            ok = process_submission(
                sub, "admin-1", bucket=BUCKET, client=client,
            )

        assert ok is True
        final = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert final["status"] == "draft_generated"
        assert final["draft_stem"] == "drafts/2026/03/good-draft"
        assert final["draft_artifacts"]["mp3"].endswith("good-draft.mp3")

    def test_failure_path_writes_generation_failed(self):
        client = FakeR2()
        _make_submission(client)
        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(False, "something broke"),
        ):
            ok = process_submission(
                sub, "admin-1", bucket=BUCKET, client=client,
            )

        assert ok is False
        final = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert final["status"] == "generation_failed"

    def test_skips_update_on_token_mismatch(self):
        """If another worker reclaimed the submission mid-generation,
        the original worker must not overwrite the new claim."""
        client = FakeR2()
        _make_submission(client)
        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")

        def clobber_token(*args, **kwargs):
            # Simulate another worker reclaiming between generation and
            # the final status write.
            _update_submission(BUCKET, client, "submissions/test-sub.json", {
                "claim_token": "interloper-token",
            })
            return (True, "drafts/2026/03/draft")

        with patch(
            "scripts.run_generation_worker._run_generation",
            side_effect=clobber_token,
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(True, {"draft_artifacts": {"mp3": "https://podcast/drafts/2026/03/draft.mp3"}}),
        ):
            ok = process_submission(
                sub, "admin-1", bucket=BUCKET, client=client,
            )

        assert ok is False
        final = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert final["claim_token"] == "interloper-token"
        assert final["status"] != "draft_generated"

    def test_no_draft_stem_marks_generation_failed(self):
        """When gen-podcast.py succeeds but stdout contains no draft
        path, the submission must be marked generation_failed rather
        than draft_generated with a missing draft_stem."""
        client = FakeR2()
        _make_submission(client)
        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, None),
        ):
            ok = process_submission(
                sub, "admin-1", bucket=BUCKET, client=client,
            )

        assert ok is False
        final = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert final["status"] == "generation_failed"
        assert "no draft stem" in final["error"].lower()

    def test_upload_failure_marks_generation_failed(self):
        client = FakeR2()
        _make_submission(client)
        sub = _read_submission(BUCKET, client, "submissions/test-sub.json")

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, "drafts/2026/03/good-draft"),
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(False, {"error": "failed to upload draft MP3"}),
        ):
            ok = process_submission(
                sub, "admin-1", bucket=BUCKET, client=client,
            )

        assert ok is False
        final = _read_submission(BUCKET, client, "submissions/test-sub.json")
        assert final["status"] == "generation_failed"
        assert "failed to upload draft MP3" in final["error"]

    def test_upload_helper_warns_on_missing_optional_artifacts(self, tmp_path, monkeypatch):
        draft_dir = tmp_path / "drafts" / "2026" / "03"
        draft_dir.mkdir(parents=True)
        mp3 = draft_dir / "example.mp3"
        mp3.write_bytes(b"mp3")

        monkeypatch.setattr("scripts.run_generation_worker.ROOT", tmp_path)

        uploads = []

        def fake_upload(client, local_path, r2_key, content_type=None, bucket=None):
            uploads.append((Path(local_path).name, r2_key))
            return f"https://podcast.do-not-panic.com/{r2_key}"

        monkeypatch.setattr(
            "r2_upload.get_r2_client",
            lambda: object(),
        )
        monkeypatch.setattr(
            "r2_upload.upload_file",
            fake_upload,
        )

        from scripts.run_generation_worker import _upload_draft_artifacts

        ok, details = _upload_draft_artifacts("drafts/2026/03/example")

        assert ok is True
        assert uploads == [("example.mp3", "drafts/2026/03/example.mp3")]
        assert details["draft_artifacts"]["mp3"].endswith("drafts/2026/03/example.mp3")
        assert any("missing optional artifact" in warning for warning in details["draft_upload_warnings"])
