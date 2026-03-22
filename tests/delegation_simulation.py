"""Test-only simulation helpers for delegation queue functional tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from delegation_backend import DelegationBackend, InMemoryDelegationStore


class FakeClock:
    """Mutable clock so simulations can model stale and abandoned claims."""

    def __init__(self, current=None):
        self.current = current or datetime(2026, 3, 21, 18, 30,
                                          tzinfo=timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, **delta_kwargs):
        self.current += timedelta(**delta_kwargs)
        return self.current


class FakeDraftStore:
    """In-memory draft registry used by delegation functional tests."""

    def __init__(self):
        self._drafts = {}
        self._next_id = 1

    def create(self, job, volunteer_id, created_at):
        draft_id = f"draft-{self._next_id}"
        self._next_id += 1
        draft = {
            "draft_id": draft_id,
            "job_id": job["job_id"],
            "title": job["title"],
            "volunteer_id": volunteer_id,
            "status": "pending_review",
            "created_at": _iso8601(created_at),
            "review": None,
            "published_artifact_id": None,
        }
        self._drafts[draft_id] = draft
        return deepcopy(draft)

    def review(self, draft_id, accepted, reviewer_id, reviewed_at,
               reason=""):
        draft = self._drafts[draft_id]
        draft["status"] = "accepted" if accepted else "rejected"
        draft["review"] = {
            "accepted": accepted,
            "reviewer_id": reviewer_id,
            "reviewed_at": _iso8601(reviewed_at),
            "reason": reason,
        }
        return deepcopy(draft)

    def attach_artifact(self, draft_id, artifact_id):
        self._drafts[draft_id]["published_artifact_id"] = artifact_id

    def get(self, draft_id):
        return deepcopy(self._drafts[draft_id])

    def snapshot(self):
        return [
            deepcopy(self._drafts[draft_id])
            for draft_id in sorted(self._drafts)
        ]


class FakeArtifactStore:
    """In-memory publisher/artifact registry for simulation tests."""

    def __init__(self):
        self._artifacts = {}
        self._next_id = 1
        self.failures = {}

    def fail_for_job(self, job_id, error):
        self.failures[job_id] = error

    def publish(self, draft, published_at):
        if draft["status"] != "accepted":
            return {
                "status": "rejected",
                "error": "draft not accepted",
            }

        error = self.failures.get(draft["job_id"])
        if error:
            return {
                "status": "failed",
                "error": error,
            }

        artifact_id = f"artifact-{self._next_id}"
        self._next_id += 1
        artifact = {
            "artifact_id": artifact_id,
            "job_id": draft["job_id"],
            "draft_id": draft["draft_id"],
            "title": draft["title"],
            "published_at": _iso8601(published_at),
            "status": "published",
        }
        self._artifacts[artifact_id] = artifact
        return deepcopy(artifact)

    def snapshot(self):
        return [
            deepcopy(self._artifacts[artifact_id])
            for artifact_id in sorted(self._artifacts)
        ]


class DelegationSimulationHarness:
    """End-to-end harness over the backend plus fake draft/publish layers."""

    def __init__(self):
        self.clock = FakeClock()
        self.store = InMemoryDelegationStore(now=self.clock)
        self.backend = DelegationBackend(self.store)
        self.drafts = FakeDraftStore()
        self.artifacts = FakeArtifactStore()

    def register_volunteer(self, volunteer_id, capabilities, locales,
                           max_claims=1, approve=True):
        registered = self.backend.handle(
            "POST",
            "/api/delegation/admin/volunteers/register",
            {
                "volunteer_id": volunteer_id,
                "capabilities": capabilities,
                "locales": locales,
                "max_claims": max_claims,
            },
        )
        if approve:
            self.backend.handle(
                "POST",
                "/api/delegation/admin/volunteers/approve",
                {
                    "volunteer_id": volunteer_id,
                    "admin_id": "admin",
                },
            )
        return registered

    def enqueue_job(self, job_id, title, locale="en-US",
                    required_capabilities=None, max_retries=3):
        return self.backend.handle(
            "POST",
            "/api/delegation/admin/jobs/enqueue",
            {
                "job_id": job_id,
                "title": title,
                "locale": locale,
                "required_capabilities": required_capabilities or [],
                "max_retries": max_retries,
            },
        )

    def sync_admin_queue(self, sections, exported_at="2026-03-21T18:30:00Z"):
        return self.backend.handle(
            "POST",
            "/api/delegation/admin/queue-sync",
            {
                "sections": sections,
                "exported_at": exported_at,
            },
        )

    def claim(self, job_id, volunteer_id, expected_version):
        return self.backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/claim",
            {
                "job_id": job_id,
                "volunteer_id": volunteer_id,
                "expected_version": expected_version,
            },
        )

    def release(self, job_id, volunteer_id, reason):
        return self.backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/release",
            {
                "job_id": job_id,
                "volunteer_id": volunteer_id,
                "reason": reason,
            },
        )

    def report_result(self, job_id, volunteer_id, success, error=""):
        return self.backend.handle(
            "POST",
            "/api/delegation/volunteer/jobs/result",
            {
                "job_id": job_id,
                "volunteer_id": volunteer_id,
                "success": success,
                "error": error,
            },
        )

    def override_claim(self, job_id, volunteer_id, expected_version,
                       reason="manual assignment"):
        return self.backend.handle(
            "POST",
            "/api/delegation/admin/jobs/override",
            {
                "job_id": job_id,
                "volunteer_id": volunteer_id,
                "expected_version": expected_version,
                "admin_id": "admin",
                "reason": reason,
            },
        )

    def complete_with_draft(self, job_id, volunteer_id):
        result = self.report_result(job_id, volunteer_id, success=True)
        if result.status != 200:
            return result, None
        draft = self.drafts.create(
            result.body["job"],
            volunteer_id,
            self.clock(),
        )
        return result, draft

    def review_draft(self, draft_id, accepted, reason=""):
        return self.drafts.review(
            draft_id,
            accepted=accepted,
            reviewer_id="editor",
            reviewed_at=self.clock(),
            reason=reason,
        )

    def publish_draft(self, draft_id):
        draft = self.drafts.get(draft_id)
        artifact = self.artifacts.publish(draft, self.clock())
        if artifact.get("artifact_id"):
            self.drafts.attach_artifact(draft_id, artifact["artifact_id"])
        return artifact

    def snapshot(self):
        exported = self.backend.handle("GET", "/api/delegation/admin/export")
        return {
            "manifest": exported.body["manifest"],
            "admin_queue": exported.body["admin_queue"],
            "drafts": self.drafts.snapshot(),
            "artifacts": self.artifacts.snapshot(),
        }

    def manifest_version(self):
        return self.store.load_manifest()["version"]


def _iso8601(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z")
    return str(value)
