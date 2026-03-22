"""Tests for the delegation and claim-based podcast generation queue."""

from datetime import datetime, timezone

import pytest

from paper_record import PaperRecord


def _now():
    return datetime(2026, 3, 21, 18, 30, tzinfo=timezone.utc)


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


class TestDelegationQueueClaims:
    def test_claim_requires_admin_approval(self):
        from delegation_queue import (
            ApprovalRequiredError,
            create_manifest,
            enqueue_job,
            register_volunteer,
            claim_job,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
        )
        manifest = enqueue_job(
            manifest,
            "job-1",
            title="KV cache episode",
            locale="en-US",
            required_capabilities=["generate"],
        )

        with pytest.raises(ApprovalRequiredError):
            claim_job(
                manifest,
                "job-1",
                "vol-a",
                now=_now(),
                expected_version=manifest["version"],
            )

    def test_claim_is_atomic_against_stale_version(self):
        from delegation_queue import (
            ClaimConflictError,
            approve_volunteer,
            create_manifest,
            enqueue_job,
            register_volunteer,
            claim_job,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(
            manifest,
            "job-1",
            title="KV cache episode",
            locale="en-US",
            required_capabilities=["generate"],
        )
        expected_version = manifest["version"]

        claimed = claim_job(
            manifest,
            "job-1",
            "vol-a",
            now=_now(),
            expected_version=expected_version,
        )

        with pytest.raises(ClaimConflictError):
            claim_job(
                claimed,
                "job-1",
                "vol-a",
                now=_now(),
                expected_version=expected_version,
            )

    def test_release_returns_job_to_queue_without_counting_failure(self):
        from delegation_queue import (
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            register_volunteer,
            release_job,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(manifest, "job-1", title="KV cache episode")
        manifest = claim_job(
            manifest,
            "job-1",
            "vol-a",
            now=_now(),
            expected_version=manifest["version"],
        )

        released = release_job(
            manifest,
            "job-1",
            "vol-a",
            reason="handing off",
            now=_now(),
        )

        job = released["jobs"]["job-1"]
        assert job["status"] == "queued"
        assert job["claimed_by"] is None
        assert job["failure_count"] == 0
        assert released["metrics"]["jobs_released"] == 1

    def test_failure_requeues_then_exhausts_retry_budget(self):
        from delegation_queue import (
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            register_volunteer,
            record_job_result,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(
            manifest,
            "job-1",
            title="KV cache episode",
            locale="en-US",
            required_capabilities=["generate"],
            max_retries=2,
        )

        for expected_failures, expected_status in [(1, "queued"), (2, "failed")]:
            manifest = claim_job(
                manifest,
                "job-1",
                "vol-a",
                now=_now(),
                expected_version=manifest["version"],
            )
            manifest = record_job_result(
                manifest,
                "job-1",
                "vol-a",
                success=False,
                now=_now(),
                error="TTS timeout",
            )
            job = manifest["jobs"]["job-1"]
            assert job["failure_count"] == expected_failures
            assert job["status"] == expected_status


class TestVolunteerRegistry:
    def test_registration_tracks_capabilities_and_claim_cap(self):
        from delegation_queue import create_manifest, register_volunteer

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate", "review"],
            locales=["en-US", "fr-FR"],
            max_claims=2,
        )

        volunteer = manifest["volunteers"]["vol-a"]
        assert volunteer["approved"] is False
        assert volunteer["max_claims"] == 2
        assert volunteer["capabilities"] == ["generate", "review"]
        assert volunteer["locales"] == ["en-US", "fr-FR"]

    def test_capability_and_claim_cap_are_enforced(self):
        from delegation_queue import (
            CapacityExceededError,
            CapabilityMismatchError,
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            register_volunteer,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
            max_claims=1,
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(
            manifest,
            "job-1",
            title="English episode",
            locale="en-US",
            required_capabilities=["generate"],
        )
        manifest = enqueue_job(
            manifest,
            "job-2",
            title="Needs review",
            locale="en-US",
            required_capabilities=["review"],
        )
        manifest = enqueue_job(
            manifest,
            "job-3",
            title="Second English episode",
            locale="en-US",
            required_capabilities=["generate"],
        )
        manifest = claim_job(
            manifest,
            "job-1",
            "vol-a",
            now=_now(),
            expected_version=manifest["version"],
        )

        with pytest.raises(CapabilityMismatchError):
            claim_job(
                manifest,
                "job-2",
                "vol-a",
                now=_now(),
                expected_version=manifest["version"],
            )

        with pytest.raises(CapacityExceededError):
            claim_job(
                manifest,
                "job-3",
                "vol-a",
                now=_now(),
                expected_version=manifest["version"],
            )

    def test_locale_matching_allows_exact_and_language_prefix(self):
        from delegation_queue import (
            LocaleMismatchError,
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            register_volunteer,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-en",
            capabilities=["generate"],
            locales=["en"],
        )
        manifest = register_volunteer(
            manifest,
            "vol-ja",
            capabilities=["generate"],
            locales=["ja-JP"],
        )
        manifest = approve_volunteer(manifest, "vol-en", admin_id="admin")
        manifest = approve_volunteer(manifest, "vol-ja", admin_id="admin")
        manifest = enqueue_job(
            manifest,
            "job-en",
            title="US English episode",
            locale="en-US",
            required_capabilities=["generate"],
        )
        manifest = enqueue_job(
            manifest,
            "job-fr",
            title="French episode",
            locale="fr-FR",
            required_capabilities=["generate"],
        )

        manifest = claim_job(
            manifest,
            "job-en",
            "vol-en",
            now=_now(),
            expected_version=manifest["version"],
        )
        assert manifest["jobs"]["job-en"]["claimed_by"] == "vol-en"

        with pytest.raises(LocaleMismatchError):
            claim_job(
                manifest,
                "job-fr",
                "vol-ja",
                now=_now(),
                expected_version=manifest["version"],
            )


class TestMetricsAndOverrides:
    def test_metrics_track_success_and_failure_by_volunteer_and_locale(self):
        from delegation_queue import (
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            record_job_result,
            register_volunteer,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(
            manifest,
            "job-1",
            title="English episode",
            locale="en-US",
            required_capabilities=["generate"],
        )
        manifest = enqueue_job(
            manifest,
            "job-2",
            title="Second episode",
            locale="en-US",
            required_capabilities=["generate"],
        )

        manifest = claim_job(
            manifest,
            "job-1",
            "vol-a",
            now=_now(),
            expected_version=manifest["version"],
        )
        manifest = record_job_result(
            manifest,
            "job-1",
            "vol-a",
            success=True,
            now=_now(),
        )
        manifest = claim_job(
            manifest,
            "job-2",
            "vol-a",
            now=_now(),
            expected_version=manifest["version"],
        )
        manifest = record_job_result(
            manifest,
            "job-2",
            "vol-a",
            success=False,
            now=_now(),
            error="renderer mismatch",
        )

        metrics = manifest["metrics"]
        assert metrics["jobs_succeeded"] == 1
        assert metrics["jobs_failed"] == 1
        assert metrics["by_volunteer"]["vol-a"] == {"success": 1, "failure": 1}
        assert metrics["by_locale"]["en-US"] == {"success": 1, "failure": 1}

    def test_admin_override_can_force_claim_without_matching_constraints(self):
        from delegation_queue import (
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            register_volunteer,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["review"],
            locales=["ja-JP"],
            max_claims=1,
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(
            manifest,
            "job-1",
            title="French generation job",
            locale="fr-FR",
            required_capabilities=["generate"],
        )

        manifest = claim_job(
            manifest,
            "job-1",
            "vol-a",
            now=_now(),
            expected_version=manifest["version"],
            admin_override={"admin_id": "admin", "reason": "manual assignment"},
        )

        job = manifest["jobs"]["job-1"]
        assert job["claimed_by"] == "vol-a"
        assert job["override"]["admin_id"] == "admin"
        assert job["override"]["reason"] == "manual assignment"


class TestSnapshotsAndExports:
    def test_snapshot_export_is_stable_and_redacts_claim_history(self):
        from delegation_queue import (
            approve_volunteer,
            claim_job,
            create_manifest,
            enqueue_job,
            export_manifest_snapshot,
            register_volunteer,
        )

        manifest = create_manifest()
        manifest = register_volunteer(
            manifest,
            "vol-a",
            capabilities=["generate"],
            locales=["en-US"],
        )
        manifest = approve_volunteer(manifest, "vol-a", admin_id="admin")
        manifest = enqueue_job(manifest, "job-1", title="Episode")
        manifest = claim_job(
            manifest,
            "job-1",
            "vol-a",
            now=_now(),
            expected_version=manifest["version"],
        )

        snapshot = export_manifest_snapshot(manifest)

        assert snapshot["version"] == manifest["version"]
        assert snapshot["jobs"][0]["job_id"] == "job-1"
        assert "history" not in snapshot["jobs"][0]
        assert snapshot["jobs"][0]["claimed_by"] == "vol-a"
        assert snapshot["volunteers"][0]["volunteer_id"] == "vol-a"

    def test_queue_export_includes_sectioned_and_flat_views_for_admin(self):
        from delegation_queue import export_admin_queue_payload

        sections = {
            "bridge": [_record("2401.00001", "Bridge Paper", 0.82)],
            "public": [_record("2401.00002", "Public Paper", 0.74)],
            "memory": [],
            "monitor": [_record("2401.00003", "Monitor Paper", 0.31)],
        }

        payload = export_admin_queue_payload(sections, exported_at="2026-03-21T18:30:00Z")

        assert payload["counts"] == {
            "bridge": 1,
            "public": 1,
            "memory": 0,
            "monitor": 1,
        }
        assert [paper["queue_section"] for paper in payload["papers"]] == [
            "bridge",
            "public",
            "monitor",
        ]
        assert payload["papers"][0]["score"] == pytest.approx(0.82)
        assert payload["sections"]["bridge"][0]["arxiv_id"] == "2401.00001"

    def test_queue_export_normalizes_dict_records_and_missing_scores(self):
        from delegation_queue import export_admin_queue_payload

        payload = export_admin_queue_payload(
            {
                "public": [{
                    "arxiv_id": "2401.00002",
                    "title": "Dict paper",
                    "abstract": "Cached data path",
                }],
            },
            exported_at="2026-03-21T18:30:00Z",
        )

        assert payload["papers"][0]["title"] == "Dict paper"
        assert payload["papers"][0]["score"] == 0.0
        assert payload["counts"]["public"] == 1
