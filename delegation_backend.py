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
    create_manifest,
    enqueue_job,
    export_admin_queue_payload,
    export_manifest_snapshot,
    record_job_result,
    register_volunteer,
    release_job,
)


TRUST_BOUNDARIES = {
    "admin_view": "authoritative operator snapshot",
    "volunteer_clients": "untrusted claimants",
    "static_exports": "semi-trusted copies, never claim from them",
}


@dataclass
class BackendResponse:
    """Simple request/response wrapper for backend flows."""

    status: int
    body: dict[str, Any]


class InMemoryDelegationStore:
    """Minimal manifest store used by tests and local backend wiring."""

    def __init__(self, now=None):
        self._now = now
        self._manifest = create_manifest()
        self._admin_queue = export_admin_queue_payload({})

    def load_manifest(self):
        return self._manifest

    def save_manifest(self, manifest):
        self._manifest = manifest

    def load_admin_queue(self):
        return self._admin_queue

    def save_admin_queue(self, payload):
        self._admin_queue = payload

    def now(self):
        if self._now is None:
            return None
        return self._now()


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
                manifest = register_volunteer(
                    self.store.load_manifest(),
                    body["volunteer_id"],
                    body["capabilities"],
                    locales=body.get("locales"),
                    max_claims=body.get("max_claims", 1),
                )
                self.store.save_manifest(manifest)
                volunteer = manifest["volunteers"][body["volunteer_id"]]
                return self._ok({
                    "volunteer": volunteer,
                    "manifest_version": manifest["version"],
                })

            if method == "POST" and path == (
                    "/api/delegation/admin/volunteers/approve"):
                manifest = approve_volunteer(
                    self.store.load_manifest(),
                    body["volunteer_id"],
                    admin_id=body["admin_id"],
                )
                self.store.save_manifest(manifest)
                volunteer = manifest["volunteers"][body["volunteer_id"]]
                return self._ok({
                    "volunteer": volunteer,
                    "manifest_version": manifest["version"],
                })

            if method == "POST" and path == "/api/delegation/admin/jobs/enqueue":
                manifest = enqueue_job(
                    self.store.load_manifest(),
                    body["job_id"],
                    title=body["title"],
                    locale=body.get("locale", "en-US"),
                    required_capabilities=body.get("required_capabilities"),
                    max_retries=body.get("max_retries", 3),
                )
                self.store.save_manifest(manifest)
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "manifest_version": manifest["version"],
                })

            if method == "POST" and path == "/api/delegation/volunteer/jobs/claim":
                manifest = claim_job(
                    self.store.load_manifest(),
                    body["job_id"],
                    body["volunteer_id"],
                    now=self.store.now(),
                    expected_version=body.get("expected_version"),
                )
                self.store.save_manifest(manifest)
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path == "/api/delegation/volunteer/jobs/release":
                manifest = release_job(
                    self.store.load_manifest(),
                    body["job_id"],
                    body["volunteer_id"],
                    reason=body.get("reason", ""),
                    now=self.store.now(),
                )
                self.store.save_manifest(manifest)
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "metrics": manifest["metrics"],
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path == "/api/delegation/volunteer/jobs/result":
                manifest = record_job_result(
                    self.store.load_manifest(),
                    body["job_id"],
                    body["volunteer_id"],
                    success=body["success"],
                    now=self.store.now(),
                    error=body.get("error", ""),
                )
                self.store.save_manifest(manifest)
                job = manifest["jobs"][body["job_id"]]
                return self._ok({
                    "job": job,
                    "metrics": manifest["metrics"],
                    "manifest_version": manifest["version"],
                    "trust_boundary": "server_manifest_authoritative",
                })

            if method == "POST" and path == "/api/delegation/admin/jobs/override":
                override = {
                    "admin_id": body["admin_id"],
                    "reason": body["reason"],
                }
                manifest = claim_job(
                    self.store.load_manifest(),
                    body["job_id"],
                    body["volunteer_id"],
                    now=self.store.now(),
                    expected_version=body.get("expected_version"),
                    admin_override=override,
                )
                self.store.save_manifest(manifest)
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

            if method == "GET" and path == "/api/delegation/admin/export":
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
