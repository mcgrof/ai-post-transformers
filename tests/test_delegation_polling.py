"""Tests for trusted operator and trusted worker polling contracts."""

from datetime import datetime, timedelta, timezone


class FakeClock:
    """Mutable clock for lease and heartbeat tests."""

    def __init__(self):
        self.current = datetime(2026, 3, 21, 18, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, **delta_kwargs):
        self.current += timedelta(**delta_kwargs)
        return self.current


class TestTrustedWorkerPolling:
    def test_trusted_worker_poll_returns_server_filtered_jobs(self):
        from delegation_backend import DelegationBackend, InMemoryDelegationStore

        backend = DelegationBackend(InMemoryDelegationStore())
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/register",
            {
                "volunteer_id": "worker-a",
                "capabilities": ["generate"],
                "locales": ["en-US"],
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/approve",
            {
                "volunteer_id": "worker-a",
                "admin_id": "operator-1",
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-1",
                "title": "English generation",
                "locale": "en-US",
                "required_capabilities": ["generate"],
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-2",
                "title": "French generation",
                "locale": "fr-FR",
                "required_capabilities": ["generate"],
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-3",
                "title": "English review",
                "locale": "en-US",
                "required_capabilities": ["review"],
            },
        )

        polled = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/poll",
            {"worker_id": "worker-a"},
        )

        assert polled.status == 200
        assert [job["job_id"] for job in polled.body["eligible_jobs"]] == ["job-1"]
        assert polled.body["active_claims"] == []
        assert polled.body["trust_boundary"] == "trusted_worker_server_filtered"

    def test_trusted_worker_claim_heartbeat_release_and_result_manage_lease(
            self):
        from delegation_backend import DelegationBackend, InMemoryDelegationStore

        clock = FakeClock()
        backend = DelegationBackend(InMemoryDelegationStore(
            now=clock,
            lease_duration_seconds=300,
        ))
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/register",
            {
                "volunteer_id": "worker-a",
                "capabilities": ["generate"],
                "locales": ["en-US"],
            },
        )
        backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/approve",
            {
                "volunteer_id": "worker-a",
                "admin_id": "operator-1",
            },
        )
        enqueued = backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": "job-1",
                "title": "KV cache episode",
                "locale": "en-US",
                "required_capabilities": ["generate"],
            },
        )

        claimed = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/jobs/claim",
            {
                "job_id": "job-1",
                "worker_id": "worker-a",
                "expected_version": enqueued.body["manifest_version"],
            },
        )
        assert claimed.status == 200
        assert claimed.body["job"]["claimed_by"] == "worker-a"
        assert claimed.body["job"]["last_heartbeat_at"] == (
            "2026-03-21T18:30:00Z"
        )
        assert claimed.body["job"]["lease_expires_at"] == (
            "2026-03-21T18:35:00Z"
        )
        assert claimed.body["lease"]["duration_seconds"] == 300

        clock.advance(minutes=2)
        heartbeated = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/jobs/heartbeat",
            {
                "job_id": "job-1",
                "worker_id": "worker-a",
            },
        )
        assert heartbeated.status == 200
        assert heartbeated.body["job"]["last_heartbeat_at"] == (
            "2026-03-21T18:32:00Z"
        )
        assert heartbeated.body["job"]["lease_expires_at"] == (
            "2026-03-21T18:37:00Z"
        )

        released = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/jobs/release",
            {
                "job_id": "job-1",
                "worker_id": "worker-a",
                "reason": "clean handoff",
            },
        )
        assert released.status == 200
        assert released.body["job"]["status"] == "queued"
        assert released.body["job"]["last_heartbeat_at"] is None
        assert released.body["job"]["lease_expires_at"] is None

        reclaimed = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/jobs/claim",
            {
                "job_id": "job-1",
                "worker_id": "worker-a",
                "expected_version": released.body["manifest_version"],
            },
        )
        assert reclaimed.status == 200

        completed = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/jobs/result",
            {
                "job_id": "job-1",
                "worker_id": "worker-a",
                "success": True,
            },
        )
        assert completed.status == 200
        assert completed.body["job"]["status"] == "completed"
        assert completed.body["job"]["last_heartbeat_at"] is None
        assert completed.body["job"]["lease_expires_at"] is None

        stale_heartbeat = backend.handle(
            "POST",
            "/api/delegation/trusted-worker/jobs/heartbeat",
            {
                "job_id": "job-1",
                "worker_id": "worker-a",
            },
        )
        assert stale_heartbeat.status == 409
        assert stale_heartbeat.body["code"] == "claim_conflict"


class TestTrustedOperatorExport:
    def test_trusted_operator_export_uses_trusted_operator_worker_language(
            self):
        from delegation_backend import DelegationBackend, InMemoryDelegationStore

        backend = DelegationBackend(InMemoryDelegationStore())

        exported = backend.handle(
            "GET",
            "/api/delegation/trusted-operator/export",
        )

        assert exported.status == 200
        assert exported.body["trust_boundaries"] == {
            "trusted_operator": "authoritative operator control plane",
            "trusted_workers": "authenticated workers claim from live state",
            "static_exports": "semi-trusted copies, never claim from them",
        }
