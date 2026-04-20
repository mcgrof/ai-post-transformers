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
    _parse_generation_result,
    _prepare_generation_inputs,
    _read_submission,
    _release_stale_claims,
    _run_generation,
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


class TestParseGenerationResult:
    def test_finds_draft_stem_in_stdout(self):
        result = type("R", (), {
            "returncode": 0,
            "stdout": "done\ndrafts/2026/04/example.mp3\n",
            "stderr": "",
        })()

        ok, stem = _parse_generation_result(result)

        assert ok is True
        assert stem == "drafts/2026/04/example"

    def test_finds_draft_stem_in_stderr_audio_line(self):
        result = type("R", (), {
            "returncode": 0,
            "stdout": "",
            "stderr": "[Podcast] Complete\n  Audio: /home/mcgrof/devel/ai-post-transformers/drafts/2026/04/example.mp3\n",
        })()

        ok, stem = _parse_generation_result(result)

        assert ok is True
        # Absolute paths must be normalized to relative R2 keys
        assert stem == "drafts/2026/04/example"

    def test_normalizes_absolute_draft_stem_to_relative(self):
        result = type("R", (), {
            "returncode": 0,
            "stdout": "[Podcast] Saved to /home/user/devel/ai-post-transformers/drafts/2026/04/2026-04-07-test-slug-abc123.mp3\n",
            "stderr": "",
        })()

        ok, stem = _parse_generation_result(result)

        assert ok is True
        assert stem == "drafts/2026/04/2026-04-07-test-slug-abc123"
        # Must NOT contain absolute path prefix
        assert not stem.startswith("/")


class TestPrepareGenerationInputs:
    def test_uses_fallback_source_text_when_present(self):
        sub = {
            "title": "AI Agent Traps",
            "urls": ["https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6372438"],
            "metadata": {
                "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6372438": {
                    "fallback_source_text": "Recovered summary text.",
                }
            },
        }

        prepared, temp_paths = _prepare_generation_inputs(sub)

        assert len(prepared) == 1
        assert prepared[0].endswith(".txt")
        assert len(temp_paths) == 1
        text = Path(prepared[0]).read_text(encoding="utf-8")
        assert "Title: AI Agent Traps" in text
        assert "Recovered summary text." in text

        for path in temp_paths:
            path.unlink(missing_ok=True)

    def test_leaves_normal_urls_unchanged(self):
        sub = {
            "urls": ["https://arxiv.org/abs/2401.12345"],
            "metadata": {},
        }

        prepared, temp_paths = _prepare_generation_inputs(sub)

        assert prepared == ["https://arxiv.org/abs/2401.12345"]
        assert temp_paths == []

    def test_run_generation_cleans_up_temp_fallback_files(self):
        sub = {
            "title": "AI Agent Traps",
            "urls": ["https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6372438"],
            "metadata": {
                "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6372438": {
                    "fallback_source_text": "Recovered summary text.",
                }
            },
        }
        seen = {}

        def fake_run_generation_simple(cmd):
            txt_inputs = [item for item in cmd if item.endswith('.txt')]
            assert len(txt_inputs) == 1
            seen['path'] = txt_inputs[0]
            assert Path(txt_inputs[0]).exists()
            return True, "drafts/2026/04/example"

        with patch(
            "scripts.run_generation_worker._run_generation_simple",
            side_effect=fake_run_generation_simple,
        ):
            ok, stem = _run_generation(sub)

        assert ok is True
        assert stem == "drafts/2026/04/example"
        assert seen['path'].endswith('.txt')
        assert not Path(seen['path']).exists()


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


class TestPrivateSubmissionAutoPublish:
    """Regression tests for the private submission auto-publish flow.

    Scenario that broke in production: a submission marked
    visibility=private finishes generation, reaches status=
    draft_generated, but no publish job is ever created. The
    drafts page correctly hides it (safety invariant) but
    /private-podcasts is empty because nothing published it.
    The episode sits in limbo forever.

    These tests ensure that:
      - private submissions create a publish job on draft_generated
      - the publish job carries visibility=private and owner
      - the submission advances to approved_for_publish
      - public submissions are NOT affected
      - auto-publish failures do not break generation
    """

    def _make_store_with_submission(self, tmp_path, sub_data):
        """Build an in-memory store seeded with a single submission."""
        from scripts.queue_store import SQLiteQueueStore
        store = SQLiteQueueStore(tmp_path / "queue.db")
        store.save_submission("submissions/priv.json", sub_data)
        return store

    def test_private_submission_creates_publish_job(self, tmp_path):
        from scripts.run_generation_worker import process_submission

        store = self._make_store_with_submission(tmp_path, {
            "urls": ["https://arxiv.org/pdf/2401.99999"],
            "status": "submitted",
            "visibility": "private",
            "owner": "mcgrof",
            "timestamp": "2026-04-20T10:00:00Z",
        })
        # claim_submission populates _key on load
        sub = {"_key": "submissions/priv.json",
               **store.load_submission("submissions/priv.json")[0]}

        created_jobs = []

        def fake_create_private(*, draft_stem, sub, owner):
            created_jobs.append({
                "draft_stem": draft_stem,
                "sub": sub,
                "owner": owner,
            })
            return "pub_test_001"

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, "drafts/2026/04/priv-draft"),
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(True, {"draft_artifacts": {}}),
        ), patch(
            "scripts.run_generation_worker._publish_draft_metadata",
            return_value=None,
        ), patch(
            "scripts.run_generation_worker._create_private_publish_job",
            side_effect=fake_create_private,
        ):
            ok = process_submission(sub, "mcgrof", store=store)

        assert ok is True
        assert len(created_jobs) == 1, (
            "exactly one private publish job must be created for a "
            "private submission that finishes generation"
        )
        assert created_jobs[0]["owner"] == "mcgrof"
        assert created_jobs[0]["draft_stem"] == "drafts/2026/04/priv-draft"
        # Submission advanced past draft_generated so the admin UI
        # shows it as handed off to the publish lane.
        final, _ = store.load_submission("submissions/priv.json")
        assert final["status"] == "approved_for_publish", (
            f"private submission must advance to approved_for_publish, "
            f"got {final['status']}"
        )

    def test_public_submission_does_not_create_publish_job(self, tmp_path):
        """A submission without visibility=private must NOT trigger the
        auto-publish path. This prevents spurious publish jobs for
        regular editorial drafts."""
        from scripts.run_generation_worker import process_submission

        store = self._make_store_with_submission(tmp_path, {
            "urls": ["https://arxiv.org/pdf/2401.88888"],
            "status": "submitted",
            "timestamp": "2026-04-20T10:00:00Z",
        })
        sub = {"_key": "submissions/priv.json",
               **store.load_submission("submissions/priv.json")[0]}

        called = []

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, "drafts/2026/04/pub-draft"),
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(True, {"draft_artifacts": {}}),
        ), patch(
            "scripts.run_generation_worker._publish_draft_metadata",
            return_value=None,
        ), patch(
            "scripts.run_generation_worker._create_private_publish_job",
            side_effect=lambda **kw: called.append(kw),
        ):
            ok = process_submission(sub, "mcgrof", store=store)

        assert ok is True
        assert called == [], (
            "auto-publish must NOT fire for a public (non-private) "
            "submission"
        )
        final, _ = store.load_submission("submissions/priv.json")
        assert final["status"] == "draft_generated", (
            "public submission must stay at draft_generated for normal "
            "editorial review"
        )

    def test_auto_publish_failure_does_not_break_generation(self, tmp_path):
        """If creating the publish job fails, generation still succeeds.
        The submission stays at draft_generated so the operator can
        manually trigger Move to Private via the admin UI."""
        from scripts.run_generation_worker import process_submission

        store = self._make_store_with_submission(tmp_path, {
            "urls": ["https://arxiv.org/pdf/2401.77777"],
            "status": "submitted",
            "visibility": "private",
            "owner": "mcgrof",
            "timestamp": "2026-04-20T10:00:00Z",
        })
        sub = {"_key": "submissions/priv.json",
               **store.load_submission("submissions/priv.json")[0]}

        def explode(**kw):
            raise RuntimeError("R2 down")

        with patch(
            "scripts.run_generation_worker._run_generation",
            return_value=(True, "drafts/2026/04/priv-draft"),
        ), patch(
            "scripts.run_generation_worker._upload_draft_artifacts",
            return_value=(True, {"draft_artifacts": {}}),
        ), patch(
            "scripts.run_generation_worker._publish_draft_metadata",
            return_value=None,
        ), patch(
            "scripts.run_generation_worker._create_private_publish_job",
            side_effect=explode,
        ):
            ok = process_submission(sub, "mcgrof", store=store)

        assert ok is True, (
            "generation must succeed even if auto-publish creation "
            "fails — the draft exists, so the operator can retry "
            "via the admin UI"
        )
        final, _ = store.load_submission("submissions/priv.json")
        # Status stays at draft_generated since we couldn't advance it.
        assert final["status"] == "draft_generated"


class TestCreatePrivatePublishJob:
    """Tests for the helper that writes the publish job record."""

    def test_job_is_tagged_private_and_owner_is_set(self, monkeypatch):
        """Fundamental safety check: the job record MUST have
        visibility=private. If this field is missing the publish
        worker's dispatch will route to the public flow."""
        from scripts.run_generation_worker import _create_private_publish_job

        saved_jobs = []

        class FakeStore:
            def save_job(self, job):
                saved_jobs.append(job)
                return job["job_id"]

        monkeypatch.setattr(
            "scripts.publish_job_store.get_publish_job_store",
            lambda mode="r2": FakeStore(),
        )

        job_id = _create_private_publish_job(
            draft_stem="drafts/2026/04/private-ep",
            sub={"title": "Private Topic"},
            owner="user@example.com",
        )

        assert len(saved_jobs) == 1
        job = saved_jobs[0]
        assert job["visibility"] == "private", (
            "CRITICAL: job MUST be tagged private, otherwise the publish "
            "worker's dispatch routes it to the public feed"
        )
        assert job["owner"] == "user@example.com"
        assert job["draft_key"] == "drafts/2026/04/private-ep.mp3"
        assert job["draft_stem"] == "drafts/2026/04/private-ep"
        assert job["title"] == "Private Topic"
        assert job_id == job["job_id"]

    def test_title_falls_back_to_stem_basename(self, monkeypatch):
        from scripts.run_generation_worker import _create_private_publish_job

        saved_jobs = []

        class FakeStore:
            def save_job(self, job):
                saved_jobs.append(job)
                return job["job_id"]

        monkeypatch.setattr(
            "scripts.publish_job_store.get_publish_job_store",
            lambda mode="r2": FakeStore(),
        )

        _create_private_publish_job(
            draft_stem="drafts/2026/04/untitled-stem",
            sub={},  # no title
            owner="someone",
        )

        assert saved_jobs[0]["title"] == "untitled-stem"
