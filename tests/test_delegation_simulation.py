"""Functional simulation tests for delegation queue behavior."""

import pytest

from paper_record import PaperRecord
from tests.delegation_simulation import DelegationSimulationHarness


def _record(arxiv_id, title, score):
    return PaperRecord(
        arxiv_id=arxiv_id,
        title=title,
        abstract=f"Abstract for {title}",
        authors=["Alice", "Bob"],
        public_interest_score=score,
        memory_score=score / 2,
        max_axis_score=score,
    )


class TestDelegationSimulationHarness:
    def test_success_flow_creates_accepted_draft_and_published_artifact(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "KV cache episode",
            required_capabilities=["generate"],
        )
        harness.sync_admin_queue({
            "public": [_record("2401.00001", "KV cache paper", 0.88)],
        })

        claimed = harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        assert claimed.status == 200

        completed, draft = harness.complete_with_draft("job-1", "vol-a")
        assert completed.status == 200
        assert draft["status"] == "pending_review"

        reviewed = harness.review_draft(draft["draft_id"], accepted=True)
        published = harness.publish_draft(draft["draft_id"])
        snapshot = harness.snapshot()

        assert reviewed["status"] == "accepted"
        assert published["status"] == "published"
        assert snapshot["manifest"]["jobs"][0]["status"] == "completed"
        assert snapshot["drafts"][0]["published_artifact_id"] == "artifact-1"
        assert snapshot["artifacts"][0]["job_id"] == "job-1"
        assert snapshot["admin_queue"]["counts"] == {"public": 1}

    def test_failure_path_records_terminal_job_without_draft_or_artifact(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Broken renderer episode",
            required_capabilities=["generate"],
            max_retries=1,
        )

        harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        failed = harness.report_result(
            "job-1",
            "vol-a",
            success=False,
            error="renderer mismatch",
        )
        snapshot = harness.snapshot()

        assert failed.status == 200
        assert failed.body["job"]["status"] == "failed"
        assert snapshot["drafts"] == []
        assert snapshot["artifacts"] == []

    def test_timeout_abandonment_can_be_reclaimed_and_stale_worker_rejected(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        harness.register_volunteer("vol-b", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Abandoned draft episode",
            required_capabilities=["generate"],
        )

        claimed = harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        assert claimed.status == 200
        harness.clock.advance(hours=2)

        reassigned = harness.override_claim(
            "job-1",
            "vol-b",
            expected_version=claimed.body["manifest_version"],
            reason="reclaim abandoned claim",
        )

        assert reassigned.status == 200
        assert reassigned.body["job"]["claimed_by"] == "vol-b"
        assert harness.store.load_manifest()["volunteers"]["vol-a"]["active_claims"] == 0
        assert harness.store.load_manifest()["volunteers"]["vol-b"]["active_claims"] == 1

        stale_result = harness.report_result("job-1", "vol-a", success=True)
        assert stale_result.status == 409

        completed, _draft = harness.complete_with_draft("job-1", "vol-b")
        assert completed.status == 200

    def test_release_and_reclaim_flow_hands_job_to_second_worker(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        harness.register_volunteer("vol-b", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Handoff episode",
            required_capabilities=["generate"],
        )

        claimed = harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        released = harness.release("job-1", "vol-a", reason="handoff")
        reclaimed = harness.claim(
            "job-1",
            "vol-b",
            expected_version=released.body["manifest_version"],
        )

        assert claimed.status == 200
        assert released.status == 200
        assert reclaimed.status == 200
        assert reclaimed.body["job"]["claimed_by"] == "vol-b"

    def test_duplicate_claim_race_rejects_second_claimant(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        harness.register_volunteer("vol-b", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Race episode",
            required_capabilities=["generate"],
        )
        shared_version = enqueued.body["manifest_version"]

        first = harness.claim("job-1", "vol-a", shared_version)
        second = harness.claim("job-1", "vol-b", shared_version)

        assert first.status == 200
        assert second.status == 409
        assert second.body["code"] == "claim_conflict"

    def test_admin_override_after_failed_untrusted_claim_can_force_assign(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["review"], ["ja-JP"])
        enqueued = harness.enqueue_job(
            "job-1",
            "French generation episode",
            locale="fr-FR",
            required_capabilities=["generate"],
        )

        rejected = harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        overridden = harness.override_claim(
            "job-1",
            "vol-a",
            expected_version=enqueued.body["manifest_version"],
            reason="break glass",
        )

        assert rejected.status == 422
        assert rejected.body["code"] == "capability_mismatch"
        assert overridden.status == 200
        assert overridden.body["job"]["override"]["reason"] == "break glass"

    def test_locale_and_capability_mismatch_block_untrusted_claims(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-locale", ["generate"], ["ja-JP"])
        harness.register_volunteer("vol-cap", ["review"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "English generation episode",
            locale="en-US",
            required_capabilities=["generate"],
        )

        locale_claim = harness.claim(
            "job-1",
            "vol-locale",
            enqueued.body["manifest_version"],
        )
        capability_claim = harness.claim(
            "job-1",
            "vol-cap",
            enqueued.body["manifest_version"],
        )

        assert locale_claim.status == 422
        assert locale_claim.body["code"] == "locale_mismatch"
        assert capability_claim.status == 422
        assert capability_claim.body["code"] == "capability_mismatch"

    def test_rejected_draft_never_publishes(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Needs editorial rewrite",
            required_capabilities=["generate"],
        )

        harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        _completed, draft = harness.complete_with_draft("job-1", "vol-a")
        reviewed = harness.review_draft(
            draft["draft_id"],
            accepted=False,
            reason="too hand-wavy",
        )
        publish_attempt = harness.publish_draft(draft["draft_id"])

        assert reviewed["status"] == "rejected"
        assert publish_attempt["status"] == "rejected"
        assert harness.snapshot()["artifacts"] == []

    def test_publish_failure_is_captured_without_creating_artifacts(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Publisher failure episode",
            required_capabilities=["generate"],
        )

        harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
        _completed, draft = harness.complete_with_draft("job-1", "vol-a")
        harness.review_draft(draft["draft_id"], accepted=True)
        harness.artifacts.fail_for_job("job-1", "r2 upload failed")

        publish_attempt = harness.publish_draft(draft["draft_id"])

        assert publish_attempt["status"] == "failed"
        assert publish_attempt["error"] == "r2 upload failed"
        assert harness.snapshot()["artifacts"] == []

    def test_snapshot_export_stays_consistent_with_live_fake_state(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        harness.enqueue_job(
            "job-1",
            "Snapshot episode",
            required_capabilities=["generate"],
        )
        harness.sync_admin_queue({
            "bridge": [_record("2401.00001", "Bridge Paper", 0.82)],
            "public": [_record("2401.00002", "Public Paper", 0.74)],
        })

        snapshot = harness.snapshot()

        assert snapshot["manifest"]["version"] == harness.manifest_version()
        assert [job["job_id"] for job in snapshot["manifest"]["jobs"]] == ["job-1"]
        assert snapshot["admin_queue"]["counts"] == {"bridge": 1, "public": 1}
        assert snapshot["drafts"] == []
        assert snapshot["artifacts"] == []

    def test_stale_snapshot_replay_cannot_override_newer_state(self):
        harness = DelegationSimulationHarness()
        harness.register_volunteer("vol-a", ["generate"], ["en-US"])
        harness.register_volunteer("vol-b", ["generate"], ["en-US"])
        enqueued = harness.enqueue_job(
            "job-1",
            "Stale replay episode",
            required_capabilities=["generate"],
        )
        stale_version = enqueued.body["manifest_version"]

        claimed = harness.claim("job-1", "vol-a", stale_version)
        assert claimed.status == 200

        released = harness.release("job-1", "vol-a", reason="fresh claim needed")
        assert released.status == 200

        stale_override = harness.override_claim(
            "job-1",
            "vol-b",
            expected_version=stale_version,
            reason="late replay",
        )
        fresh_override = harness.override_claim(
            "job-1",
            "vol-b",
            expected_version=released.body["manifest_version"],
            reason="fresh replay",
        )

        assert stale_override.status == 409
        assert fresh_override.status == 200


def test_fake_harness_is_isolated_from_real_publish_side_effects(tmp_path):
    harness = DelegationSimulationHarness()
    harness.register_volunteer("vol-a", ["generate"], ["en-US"])
    enqueued = harness.enqueue_job(
        "job-1",
        "No side effects episode",
        required_capabilities=["generate"],
    )

    harness.claim("job-1", "vol-a", enqueued.body["manifest_version"])
    _completed, draft = harness.complete_with_draft("job-1", "vol-a")
    harness.review_draft(draft["draft_id"], accepted=True)
    harness.publish_draft(draft["draft_id"])

    assert not list(tmp_path.iterdir())
    assert harness.snapshot()["artifacts"][0]["status"] == "published"
