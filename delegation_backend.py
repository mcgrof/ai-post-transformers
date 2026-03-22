"""Backend adapter for delegation queue admin and volunteer flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from delegation_queue import (
    ApprovalRequiredError,
    CapabilityMismatchError,
    CapacityExceededError,
    ClaimConflictError,
    LocaleMismatchError,
    approve_volunteer,
    claim_job,
    heartbeat_job,
    enqueue_job,
    export_admin_queue_payload,
    export_manifest_snapshot,
    record_job_result,
    register_volunteer,
    release_job,
)
from delegation_store import InMemoryDelegationStateStore


TRUST_BOUNDARIES = {
    "trusted_operator": "authoritative operator control plane",
    "trusted_workers": "authenticated workers claim from live state",
    "static_exports": "semi-trusted copies, never claim from them",
}


@dataclass
class BackendResponse:
    """Simple request/response wrapper for backend flows."""

    status: int
    body: dict[str, Any]


class InMemoryDelegationStore(InMemoryDelegationStateStore):
    """Backward-compatible in-memory delegation store."""


class DelegationBackend:
    """Request-oriented backend facade over delegation queue helpers."""

    def __init__(self, store):
        self.store = store

    def handle(self, method, path, body=None):
        """Handle one admin or volunteer request."""
        body = body or {}

        try:
            if method == "POST" and path == (
                    "/api/delegation/admin/volunteers/register"):
                manifest = self.store.mutate_manifest(
                    lambda current: register_volunteer(
                        current,
                        body["volunteer_id"],
                        body["capabilities"],
                        locales=body.get("locales"),
                        max_claims=body.get("max_claims", 1),
                    )
                )
                volunteer = manifest["volunteers"][body["volunteer_id"]]
                return self._ok({
                    "volunteer": volunteer,
                    "manifest_version": manifest["version"],
                })

            if method == "POST" and path == (
                    "/api/delegation/admin/volunteers/approve"):
                manifest = self.store.mutate_manifest(
                    lambda current: approve_volunteer(
                        current,
                        body["volunteer_id"],
                        admin_id=body["admin_id"],
                    )
                )
                volunteer = manifest["volunteers"][body["volunteer_id"]]
                return self._ok({
                    "volunteer": volunteer,
                    "manifest_version": manifest["version"],
                })

            if method == "POST" and path == "/api/delegation/admin/jobs/enqueue":
                manifest = self.store.mutate_manifest(
                    lambda current: enqueue_job(
                        current,
                        body["job_id"],
                        title=body["title"],
                        locale=body.get("locale", "en-US"),
                        required_capabilities=body.get(
                            "required_capabilities"),
                        max_retries=body.get("max_retries", 3),
                    )
                )
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "manifest_version": manifest["version"],
                })

            if method == "POST" and path in (
                    "/api/delegation/volunteer/jobs/claim",
                    "/api/delegation/trusted-worker/jobs/claim"):
                volunteer_id = body.get("volunteer_id", body.get("worker_id"))
                manifest = self.store.mutate_manifest(
                    lambda current: claim_job(
                        current,
                        body["job_id"],
                        volunteer_id,
                        now=self.store.now(),
                        expected_version=body.get("expected_version"),
                        lease_duration_seconds=self.store.
                        lease_duration_seconds(),
                    ),
                    expected_version=body.get("expected_version"),
                )
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "lease": self._lease_payload(job),
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path in (
                    "/api/delegation/volunteer/jobs/release",
                    "/api/delegation/trusted-worker/jobs/release"):
                volunteer_id = body.get("volunteer_id", body.get("worker_id"))
                manifest = self.store.mutate_manifest(
                    lambda current: release_job(
                        current,
                        body["job_id"],
                        volunteer_id,
                        reason=body.get("reason", ""),
                        now=self.store.now(),
                    )
                )
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "metrics": manifest["metrics"],
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path in (
                    "/api/delegation/volunteer/jobs/result",
                    "/api/delegation/trusted-worker/jobs/result"):
                volunteer_id = body.get("volunteer_id", body.get("worker_id"))
                manifest = self.store.mutate_manifest(
                    lambda current: record_job_result(
                        current,
                        body["job_id"],
                        volunteer_id,
                        success=body["success"],
                        now=self.store.now(),
                        error=body.get("error", ""),
                    )
                )
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "metrics": manifest["metrics"],
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path == (
                    "/api/delegation/trusted-worker/jobs/heartbeat"):
                manifest = self.store.mutate_manifest(
                    lambda current: heartbeat_job(
                        current,
                        body["job_id"],
                        body["worker_id"],
                        now=self.store.now(),
                        lease_duration_seconds=self.store.
                        lease_duration_seconds(),
                    )
                )
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "lease": self._lease_payload(job),
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path == "/api/delegation/admin/jobs/override":
                override = {
                    "admin_id": body["admin_id"],
                    "reason": body["reason"],
                }
                manifest = self.store.mutate_manifest(
                    lambda current: claim_job(
                        current,
                        body["job_id"],
                        body["volunteer_id"],
                        now=self.store.now(),
                        expected_version=body.get("expected_version"),
                        admin_override=override,
                        lease_duration_seconds=self.store.
                        lease_duration_seconds(),
                    ),
                    expected_version=body.get("expected_version"),
                )
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path == "/api/delegation/admin/queue-sync":
                payload = export_admin_queue_payload(
                    body.get("sections", {}),
                    exported_at=body.get("exported_at"),
                )
                self.store.save_admin_queue(payload)
                return self._ok(payload)

            if method == "POST" and path == "/api/delegation/trusted-worker/poll":
                return self._ok(self._worker_poll_payload(body["worker_id"]))

            if method == "GET" and path in (
                    "/api/delegation/admin/export",
                    "/api/delegation/trusted-operator/export"):
                return self._ok({
                    "manifest": export_manifest_snapshot(
                        self.store.load_manifest()),
                    "admin_queue": self.store.load_admin_queue(),
                    "trust_boundaries": TRUST_BOUNDARIES,
                })

            return BackendResponse(404, {"error": "not found"})
        except KeyError as exc:
            return BackendResponse(400, {
                "error": f"missing field: {exc.args[0]}",
                "code": "bad_request",
            })
        except ApprovalRequiredError as exc:
            return self._error("approval_required", str(exc), 403)
        except ClaimConflictError as exc:
            return self._error("claim_conflict", str(exc), 409)
        except CapabilityMismatchError as exc:
            return self._error("capability_mismatch", str(exc), 422)
        except LocaleMismatchError as exc:
            return self._error("locale_mismatch", str(exc), 422)
        except CapacityExceededError as exc:
            return self._error("capacity_exceeded", str(exc), 422)

    def _ok(self, body):
        return BackendResponse(200, body)

    def _error(self, code, message, status):
        return BackendResponse(status, {
            "error": message,
            "code": code,
            "manifest_version": self.store.load_manifest()["version"],
            "trust_boundary": "server_manifest_authoritative",
        })

    def _worker_poll_payload(self, worker_id):
        manifest = self.store.load_manifest()
        worker = manifest["volunteers"][worker_id]
        if not worker.get("approved"):
            raise ApprovalRequiredError(worker_id)

        eligible_jobs = []
        active_claims = []
        at_capacity = worker["active_claims"] >= worker["max_claims"]
        for job_id in sorted(manifest["jobs"]):
            job = manifest["jobs"][job_id]
            if job["claimed_by"] == worker_id and job["status"] == "claimed":
                active_claims.append(self._public_job(job))
                continue
            if at_capacity or job["status"] != "queued":
                continue
            if not self._worker_can_claim(job, worker):
                continue
            eligible_jobs.append(self._public_job(job))

        return {
            "worker": worker,
            "eligible_jobs": eligible_jobs,
            "active_claims": active_claims,
            "manifest_version": manifest["version"],
            "lease_duration_seconds": self.store.lease_duration_seconds(),
            "trust_boundary": "trusted_worker_server_filtered",
        }

    def _worker_can_claim(self, job, worker):
        required = set(job["required_capabilities"])
        actual = set(worker["capabilities"])
        if not required.issubset(actual):
            return False
        return self._locale_matches(job["locale"], worker["locales"])

    def _locale_matches(self, job_locale, worker_locales):
        if "*" in worker_locales or job_locale in worker_locales:
            return True
        return job_locale.split("-", 1)[0] in worker_locales

    def _lease_payload(self, job):
        return {
            "duration_seconds": self.store.lease_duration_seconds(),
            "last_heartbeat_at": job["last_heartbeat_at"],
            "expires_at": job["lease_expires_at"],
        }

    def _public_job(self, job):
        visible = dict(job)
        visible.pop("history", None)
        return visible
