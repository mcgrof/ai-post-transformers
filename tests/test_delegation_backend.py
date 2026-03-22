"""Integration tests for delegation admin/backend request flows."""

from datetime import datetime, timezone

from paper_record import PaperRecord


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


def _now():
    return datetime(2026, 3, 21, 18, 30, tzinfo=timezone.utc)


class TestDelegationBackendFlows:
    def test_registration_approval_and_claim_release_result_flow(self):
        from delegation_backend import (
            DelegationBackend,
            InMemoryDelegationStore,
        )

        backend = DelegationBackend(
            InMemoryDelegationStore(now=_now),
        )

        registered = backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/register",
            {
                "volunteer_id": "vol-a",
                "capabilities": ["generate"],
                "locales": ["en-US"],
                "max_claims": 1,
            },
        )
        assert registered.status == 200
        assert registered.body["volunteer"]["approved"] is False
        assert registered.body["manifest_version"] == 1

        approved = backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/approve",
            {
                "volunteer_id": "vol-a",
                "admin_id": "admin",
            },
        )
        assert approved.status == 200
        assert approved.body["volunteer"]["approved"] is True
        assert approved.body["volunteer"]["approved_by"] == "admin"

        enqueued = backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-1",
                "title": "KV cache episode",
                "locale": "en-US",
                "required_capabilities": ["generate"],
                "max_retries": 2,
            },
        )
        assert enqueued.status == 200
        assert enqueued.body["job"]["status"] == "queued"

        claimed = backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/claim",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "expected_version": enqueued.body["manifest_version"],
            },
        )
        assert claimed.status == 200
        assert claimed.body["job"]["claimed_by"] == "vol-a"
        assert claimed.body["manifest_version"] == 4

        released = backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/release",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "reason": "handoff after partial work",
            },
        )
        assert released.status == 200
        assert released.body["job"]["status"] == "queued"
        assert released.body["metrics"]["jobs_released"] == 1

        claimed_again = backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/claim",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "expected_version": released.body["manifest_version"],
            },
        )
        assert claimed_again.status == 200

        completed = backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/result",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "success": True,
            },
        )
        assert completed.status == 200
        assert completed.body["job"]["status"] == "completed"
        assert completed.body["metrics"]["jobs_succeeded"] == 1

    def test_failure_and_override_flow_keep_authoritative_manifest_versioning(self):
        from delegation_backend import (
            DelegationBackend,
            InMemoryDelegationStore,
        )

        backend = DelegationBackend(
            InMemoryDelegationStore(now=_now),
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/register",
            {
                "volunteer_id": "vol-a",
                "capabilities": ["review"],
                "locales": ["ja-JP"],
                "max_claims": 1,
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/approve",
            {
                "volunteer_id": "vol-a",
                "admin_id": "admin",
            },
        )
        enqueued = backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-1",
                "title": "French generation job",
                "locale": "fr-FR",
                "required_capabilities": ["generate"],
                "max_retries": 1,
            },
        )

        rejected = backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/claim",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "expected_version": enqueued.body["manifest_version"],
            },
        )
        assert rejected.status == 422
        assert rejected.body["code"] == "capability_mismatch"
        assert rejected.body["trust_boundary"] == "server_manifest_authoritative"

        overridden = backend.handle(
            "POST",
            "/api/delegation/admin/jobs/override",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "expected_version": enqueued.body["manifest_version"],
                "admin_id": "admin",
                "reason": "break-glass recovery",
            },
        )
        assert overridden.status == 200
        assert overridden.body["job"]["override"]["reason"] == (
            "break-glass recovery"
        )

        failed = backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/result",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "success": False,
                "error": "renderer mismatch",
            },
        )
        assert failed.status == 200
        assert failed.body["job"]["status"] == "failed"
        assert failed.body["job"]["failure_count"] == 1
        assert failed.body["metrics"]["jobs_failed"] == 1

        stale_override = backend.handle(
            "POST",
            "/api/delegation/admin/jobs/override",
            {
                "job_id": "job-1",
                "volunteer_id": "vol-a",
                "expected_version": enqueued.body["manifest_version"],
                "admin_id": "admin",
                "reason": "late retry",
            },
        )
        assert stale_override.status == 409
        assert stale_override.body["code"] == "claim_conflict"
        assert stale_override.body["manifest_version"] == failed.body["manifest_version"]

    def test_admin_export_bundle_combines_manifest_snapshot_and_queue_payload(self):
        from delegation_backend import (
            DelegationBackend,
            InMemoryDelegationStore,
        )

        backend = DelegationBackend(
            InMemoryDelegationStore(now=_now),
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/register",
            {
                "volunteer_id": "vol-a",
                "capabilities": ["generate"],
                "locales": ["en-US"],
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-1",
                "title": "KV cache episode",
                "locale": "en-US",
                "required_capabilities": ["generate"],
            },
        )
        synced = backend.handle(
            "POST",
            "/api/delegation/admin/queue-sync",
            {
                "sections": {
                    "bridge": [_record("2401.00001", "Bridge Paper", 0.82)],
                    "public": [_record("2401.00002", "Public Paper", 0.74)],
                },
                "exported_at": "2026-03-21T18:30:00Z",
            },
        )
        assert synced.status == 200
        assert synced.body["counts"] == {"bridge": 1, "public": 1}

        exported = backend.handle("GET", "/api/delegation/admin/export")
        assert exported.status == 200
        assert exported.body["manifest"]["jobs"][0]["job_id"] == "job-1"
        assert exported.body["admin_queue"]["papers"][0]["queue_section"] == "bridge"
        assert exported.body["trust_boundaries"] == {
            "admin_view": "authoritative operator snapshot",
            "volunteer_clients": "untrusted claimants",
            "static_exports": "semi-trusted copies, never claim from them",
        }
