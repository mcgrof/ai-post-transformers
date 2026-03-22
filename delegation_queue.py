"""Delegation and claim-based queue helpers for podcast generation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


class DelegationQueueError(RuntimeError):
    """Base error for delegation queue operations."""


class ApprovalRequiredError(DelegationQueueError):
    """Raised when an unapproved volunteer attempts to claim work."""


class ClaimConflictError(DelegationQueueError):
    """Raised when a claim operates on stale manifest state."""


class CapabilityMismatchError(DelegationQueueError):
    """Raised when a volunteer lacks a job capability requirement."""


class CapacityExceededError(DelegationQueueError):
    """Raised when a volunteer exceeds the configured claim cap."""


class LocaleMismatchError(DelegationQueueError):
    """Raised when a volunteer does not match a locale requirement."""


def create_manifest():
    """Return an empty queue manifest."""
    return {
        "version": 0,
        "jobs": {},
        "volunteers": {},
        "metrics": {
            "jobs_claimed": 0,
            "jobs_released": 0,
            "jobs_succeeded": 0,
            "jobs_failed": 0,
            "by_volunteer": {},
            "by_locale": {},
        },
    }


def register_volunteer(manifest, volunteer_id, capabilities,
                       locales=None, max_claims=1):
    """Register or update a volunteer."""
    state = deepcopy(manifest)
    state["volunteers"][volunteer_id] = {
        "volunteer_id": volunteer_id,
        "approved": state["volunteers"].get(
            volunteer_id, {}).get("approved", False),
        "capabilities": sorted(set(capabilities)),
        "locales": list(locales or ["*"]),
        "max_claims": max_claims,
        "active_claims": state["volunteers"].get(
            volunteer_id, {}).get("active_claims", 0),
    }
    state["version"] += 1
    return state


def approve_volunteer(manifest, volunteer_id, admin_id):
    """Approve a volunteer for claiming work."""
    state = deepcopy(manifest)
    volunteer = deepcopy(state["volunteers"][volunteer_id])
    volunteer["approved"] = True
    volunteer["approved_by"] = admin_id
    state["volunteers"][volunteer_id] = volunteer
    state["version"] += 1
    return state


def enqueue_job(manifest, job_id, title, locale="en-US",
                required_capabilities=None, max_retries=3):
    """Add a queue job to the manifest."""
    state = deepcopy(manifest)
    state["jobs"][job_id] = {
        "job_id": job_id,
        "title": title,
        "locale": locale,
        "required_capabilities": sorted(set(required_capabilities or [])),
        "status": "queued",
        "claimed_by": None,
        "claimed_at": None,
        "failure_count": 0,
        "max_retries": max_retries,
        "override": None,
        "history": [],
    }
    state["version"] += 1
    return state


def claim_job(manifest, job_id, volunteer_id, now=None, expected_version=None,
              admin_override=None):
    """Claim a job if the manifest version and volunteer fit."""
    _require_version(manifest, expected_version)

    state = deepcopy(manifest)
    volunteer = deepcopy(state["volunteers"][volunteer_id])
    job = deepcopy(state["jobs"][job_id])

    prior_claimant_id = job["claimed_by"]
    prior_claimant = None
    if prior_claimant_id is not None:
        prior_claimant = deepcopy(state["volunteers"][prior_claimant_id])

    if job["status"] != "queued":
        if not admin_override or job["status"] != "claimed":
            raise ClaimConflictError(f"job {job_id} is not claimable")

    if not admin_override:
        if not volunteer.get("approved"):
            raise ApprovalRequiredError(volunteer_id)
        _check_capabilities(job, volunteer)
        _check_locale(job, volunteer)
        if volunteer["active_claims"] >= volunteer["max_claims"]:
            raise CapacityExceededError(volunteer_id)

    ts = _iso8601(now)
    history_action = "claimed"
    if job["status"] == "claimed" and admin_override:
        history_action = "reclaimed"
        if prior_claimant and prior_claimant_id != volunteer_id:
            prior_claimant["active_claims"] = max(
                0, prior_claimant["active_claims"] - 1)
            state["volunteers"][prior_claimant_id] = prior_claimant
        if prior_claimant_id != volunteer_id:
            volunteer["active_claims"] += 1
    else:
        volunteer["active_claims"] += 1

    job["status"] = "claimed"
    job["claimed_by"] = volunteer_id
    job["claimed_at"] = ts
    job["override"] = deepcopy(admin_override) if admin_override else None
    job["history"].append({
        "action": history_action,
        "volunteer_id": volunteer_id,
        "prior_volunteer_id": prior_claimant_id,
        "timestamp": ts,
        "override": deepcopy(admin_override) if admin_override else None,
    })

    state["volunteers"][volunteer_id] = volunteer
    state["jobs"][job_id] = job
    state["metrics"]["jobs_claimed"] += 1
    state["version"] += 1
    return state


def release_job(manifest, job_id, volunteer_id, reason="", now=None):
    """Release a claimed job back to the queue."""
    state = deepcopy(manifest)
    volunteer = deepcopy(state["volunteers"][volunteer_id])
    job = deepcopy(state["jobs"][job_id])
    if job["claimed_by"] != volunteer_id:
        raise ClaimConflictError(f"{volunteer_id} does not own {job_id}")

    ts = _iso8601(now)
    volunteer["active_claims"] = max(0, volunteer["active_claims"] - 1)
    job["status"] = "queued"
    job["claimed_by"] = None
    job["claimed_at"] = None
    job["history"].append({
        "action": "released",
        "volunteer_id": volunteer_id,
        "reason": reason,
        "timestamp": ts,
    })

    state["volunteers"][volunteer_id] = volunteer
    state["jobs"][job_id] = job
    state["metrics"]["jobs_released"] += 1
    state["version"] += 1
    return state


def record_job_result(manifest, job_id, volunteer_id, success, now=None,
                      error=""):
    """Record a claimed job result and update queue metrics."""
    state = deepcopy(manifest)
    volunteer = deepcopy(state["volunteers"][volunteer_id])
    job = deepcopy(state["jobs"][job_id])
    if job["claimed_by"] != volunteer_id:
        raise ClaimConflictError(f"{volunteer_id} does not own {job_id}")

    ts = _iso8601(now)
    volunteer["active_claims"] = max(0, volunteer["active_claims"] - 1)

    if success:
        job["status"] = "completed"
        state["metrics"]["jobs_succeeded"] += 1
        _bump_metric(state["metrics"]["by_volunteer"], volunteer_id, "success")
        _bump_metric(state["metrics"]["by_locale"], job["locale"], "success")
        action = "completed"
    else:
        job["failure_count"] += 1
        state["metrics"]["jobs_failed"] += 1
        _bump_metric(state["metrics"]["by_volunteer"], volunteer_id, "failure")
        _bump_metric(state["metrics"]["by_locale"], job["locale"], "failure")
        if job["failure_count"] >= job["max_retries"]:
            job["status"] = "failed"
        else:
            job["status"] = "queued"
        action = "failed"

    job["history"].append({
        "action": action,
        "volunteer_id": volunteer_id,
        "timestamp": ts,
        "error": error,
    })
    job["claimed_by"] = None
    job["claimed_at"] = None

    state["volunteers"][volunteer_id] = volunteer
    state["jobs"][job_id] = job
    state["version"] += 1
    return state


def export_manifest_snapshot(manifest):
    """Export a stable manifest snapshot for rendering or debugging."""
    jobs = []
    for job_id in sorted(manifest["jobs"]):
        job = deepcopy(manifest["jobs"][job_id])
        job.pop("history", None)
        jobs.append(job)

    volunteers = []
    for volunteer_id in sorted(manifest["volunteers"]):
        volunteers.append(deepcopy(manifest["volunteers"][volunteer_id]))

    return {
        "version": manifest["version"],
        "jobs": jobs,
        "volunteers": volunteers,
        "metrics": deepcopy(manifest["metrics"]),
    }


def export_admin_queue_payload(sections, exported_at=None):
    """Export queue data in both sectioned and flat forms."""
    normalized_sections = {}
    flat_papers = []
    counts = {}

    for section_name, records in sections.items():
        items = []
        for record in records:
            item = _record_to_dict(record)
            items.append(item)
            flat_papers.append({
                "queue_section": section_name,
                "score": _paper_score(item),
                **item,
            })
        normalized_sections[section_name] = items
        counts[section_name] = len(items)

    return {
        "exported_at": exported_at or _iso8601(None),
        "sections": normalized_sections,
        "papers": flat_papers,
        "counts": counts,
    }


def _record_to_dict(record):
    if isinstance(record, dict):
        return deepcopy(record)
    if hasattr(record, "to_dict"):
        return record.to_dict()
    if hasattr(record, "__dict__"):
        return deepcopy(record.__dict__)
    raise TypeError(f"Unsupported queue record: {type(record)!r}")


def _paper_score(record):
    for key in ("max_axis_score", "public_interest_score", "memory_score"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _require_version(manifest, expected_version):
    if expected_version is None:
        return
    if manifest["version"] != expected_version:
        raise ClaimConflictError(
            f"stale manifest version {expected_version}, "
            f"current version is {manifest['version']}"
        )


def _check_capabilities(job, volunteer):
    required = set(job["required_capabilities"])
    actual = set(volunteer["capabilities"])
    if not required.issubset(actual):
        raise CapabilityMismatchError(
            f"{volunteer['volunteer_id']} lacks {sorted(required - actual)}"
        )


def _check_locale(job, volunteer):
    if _locale_matches(job["locale"], volunteer["locales"]):
        return
    raise LocaleMismatchError(
        f"{volunteer['volunteer_id']} does not match {job['locale']}"
    )


def _locale_matches(job_locale, volunteer_locales):
    if "*" in volunteer_locales:
        return True
    if job_locale in volunteer_locales:
        return True
    job_language = job_locale.split("-", 1)[0]
    return job_language in volunteer_locales


def _bump_metric(buckets, key, field):
    current = buckets.setdefault(key, {"success": 0, "failure": 0})
    current[field] += 1


def _iso8601(value):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z")
    return str(value)
