"""Shared publish job record helpers for the admin publish workflow."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROGRESS_STATES = {"pending", "running", "done", "failed", "skipped"}
JOB_STATES = {
    "approved_for_publish",
    "publish_claimed",
    "publish_running",
    "publish_released",
    "publish_failed",
    "publish_completed",
}
STEP_ORDER = ["publish", "viz", "cover", "site", "verify"]
TERMINAL_STATES = {"publish_failed", "publish_completed"}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def jobs_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "publish-jobs"


def results_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "publish-results"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def make_job_id(now: datetime | None = None) -> str:
    now = now or utcnow()
    return now.strftime("pub_%Y_%m_%d_%H%M%S")


def draft_stem_from_key(draft_key: str | None) -> str | None:
    if not draft_key:
        return None
    stem = str(draft_key).rstrip("/")
    for ext in (".mp3", ".txt", ".json", ".srt", ".png"):
        if stem.endswith(ext):
            return stem[: -len(ext)]
    return stem


def _default_progress() -> dict[str, str]:
    return {step: "pending" for step in STEP_ORDER}


def _default_requirements() -> dict[str, bool]:
    return {
        "viz": True,
        "cover": True,
        "publish_site": True,
        "verify": True,
    }


def _default_artifacts() -> dict[str, str | None]:
    return {
        "audio_url": None,
        "srt_url": None,
        "page_url": None,
        "viz_url": None,
        "cover_url": None,
        "thumb_url": None,
    }


def _append_history(job: dict, action: str, **extra) -> None:
    entry = {"timestamp": iso_now(), "action": action}
    entry.update(extra)
    job.setdefault("history", []).append(entry)


def make_job_record(
    *,
    draft_key: str,
    title: str | None = None,
    episode_id: int | None = None,
    draft_id: int | None = None,
    draft_stem: str | None = None,
    approved_by_admin_id: str | None = None,
    approved_by_name: str | None = None,
    job_id: str | None = None,
    created_at: str | None = None,
    requirements: dict | None = None,
) -> dict:
    created_at = created_at or iso_now()
    draft_stem = draft_stem or draft_stem_from_key(draft_key)
    job = {
        "job_id": job_id or make_job_id(),
        "episode_id": episode_id,
        "draft_id": draft_id,
        "draft_key": draft_key,
        "draft_stem": draft_stem,
        "title": title or (Path(draft_stem).name if draft_stem else draft_key),
        "state": "approved_for_publish",
        "created_at": created_at,
        "updated_at": created_at,
        "approved_by_admin_id": approved_by_admin_id,
        "approved_by_name": approved_by_name,
        "claimed_by_admin_id": None,
        "claimed_by_name": None,
        "claimed_at": None,
        "lease_expires_at": None,
        "last_heartbeat_at": None,
        "released_at": None,
        "release_reason": None,
        "requirements": deepcopy(requirements or _default_requirements()),
        "progress": _default_progress(),
        "step_timestamps": {},
        "artifacts": _default_artifacts(),
        "error": None,
        "history": [],
    }
    _append_history(job, "created", state=job["state"])
    return job


def validate_job(job: dict) -> None:
    state = job.get("state")
    if state not in JOB_STATES:
        raise ValueError(f"invalid job state: {state}")
    for step, value in job.get("progress", {}).items():
        if step not in STEP_ORDER:
            raise ValueError(f"invalid progress step: {step}")
        if value not in PROGRESS_STATES:
            raise ValueError(f"invalid progress state for {step}: {value}")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def job_path(job_or_id: str | dict, root: Path | None = None) -> Path:
    if isinstance(job_or_id, dict):
        job_id = job_or_id["job_id"]
    else:
        job_id = job_or_id
    if job_id.endswith(".json"):
        return Path(job_id)
    return jobs_dir(root) / f"{job_id}.json"


def result_path(job_or_id: str | dict, root: Path | None = None) -> Path:
    if isinstance(job_or_id, dict):
        job_id = job_or_id["job_id"]
    else:
        job_id = job_or_id
    return results_dir(root) / f"{job_id}.json"


def _resolve_store(*, store=None, root: Path | None = None):
    if store is not None:
        return store
    from scripts.publish_job_store import get_publish_job_store

    return get_publish_job_store(root=root)


def save_job(job: dict, root: Path | None = None, store=None):
    validate_job(job)
    job["updated_at"] = iso_now()
    resolved_store = _resolve_store(store=store, root=root)
    return resolved_store.save_job(job)


def load_job(path_or_id: str | Path, root: Path | None = None, store=None) -> dict:
    path = Path(path_or_id)
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            job = json.load(handle)
        validate_job(job)
        return job
    resolved_store = _resolve_store(store=store, root=root)
    if resolved_store.__class__.__name__ != "LocalPublishJobStore":
        return resolved_store.load_job(path_or_id)
    path = job_path(str(path_or_id), root=root)
    with open(path, encoding="utf-8") as handle:
        job = json.load(handle)
    validate_job(job)
    return job


def save_result(job: dict, verification: dict | None = None, root: Path | None = None, store=None):
    result = {
        "job_id": job["job_id"],
        "state": job["state"],
        "admin": {
            "approved_by_admin_id": job.get("approved_by_admin_id"),
            "approved_by_name": job.get("approved_by_name"),
            "claimed_by_admin_id": job.get("claimed_by_admin_id"),
            "claimed_by_name": job.get("claimed_by_name"),
        },
        "artifacts": deepcopy(job.get("artifacts", {})),
        "step_timestamps": deepcopy(job.get("step_timestamps", {})),
        "error": deepcopy(job.get("error")),
        "verification": deepcopy(verification or {}),
        "updated_at": iso_now(),
    }
    resolved_store = _resolve_store(store=store, root=root)
    return resolved_store.save_result(job["job_id"], result)


def list_jobs(root: Path | None = None, store=None) -> list[dict]:
    resolved_store = _resolve_store(store=store, root=root)
    return resolved_store.list_jobs()


def lease_is_active(job: dict, now: datetime | None = None) -> bool:
    now = now or utcnow()
    lease_expires_at = job.get("lease_expires_at")
    if not lease_expires_at:
        return False
    return datetime.fromisoformat(lease_expires_at) > now




def _step_index(step: str) -> int:
    if step not in STEP_ORDER:
        raise ValueError(f"unknown step: {step}")
    return STEP_ORDER.index(step)


def _running_steps(job: dict) -> list[str]:
    return [step for step, value in job.get("progress", {}).items() if value == "running"]


def _ensure_nonterminal(job: dict) -> None:
    if job.get("state") in TERMINAL_STATES:
        raise ValueError(f"job is already terminal: {job.get('state')}")


def _ensure_claimed(job: dict) -> None:
    if not job.get("claimed_by_admin_id"):
        raise ValueError("job must be claimed before running publish steps")


def _ensure_prior_steps_complete(job: dict, step: str) -> None:
    idx = _step_index(step)
    progress = job.get("progress", {})
    for earlier in STEP_ORDER[:idx]:
        if progress.get(earlier) not in {"done", "skipped"}:
            raise ValueError(f"cannot operate on {step} before {earlier} is complete")


def claim_job(
    job: dict,
    *,
    admin_id: str,
    admin_name: str | None = None,
    lease_seconds: int = 900,
) -> dict:
    _ensure_nonterminal(job)
    if _running_steps(job):
        raise ValueError("cannot claim a job while a publish step is running")
    if lease_is_active(job) and job.get("claimed_by_admin_id") != admin_id:
        raise ValueError("job is already claimed by another admin")
    now = utcnow()
    job["state"] = "publish_claimed"
    job["claimed_by_admin_id"] = admin_id
    job["claimed_by_name"] = admin_name
    job["claimed_at"] = now.isoformat()
    job["last_heartbeat_at"] = now.isoformat()
    job["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
    job["error"] = None
    _append_history(job, "claimed", admin_id=admin_id, admin_name=admin_name)
    return job


def heartbeat_job(job: dict, *, admin_id: str, lease_seconds: int = 900) -> dict:
    _ensure_nonterminal(job)
    if job.get("claimed_by_admin_id") != admin_id:
        raise ValueError("cannot heartbeat job claimed by another admin")
    now = utcnow()
    job["last_heartbeat_at"] = now.isoformat()
    job["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
    _append_history(job, "heartbeat", admin_id=admin_id)
    return job


def release_job(job: dict, *, admin_id: str, reason: str | None = None) -> dict:
    if job.get("state") == "publish_completed":
        raise ValueError("cannot release a completed job")
    if job.get("claimed_by_admin_id") not in (None, admin_id):
        raise ValueError("cannot release job claimed by another admin")
    for step in _running_steps(job):
        job["progress"][step] = "pending"
    job["state"] = "publish_released"
    job["released_at"] = iso_now()
    job["release_reason"] = reason
    job["claimed_by_admin_id"] = None
    job["claimed_by_name"] = None
    job["claimed_at"] = None
    job["lease_expires_at"] = None
    job["last_heartbeat_at"] = None
    _append_history(job, "released", admin_id=admin_id, reason=reason)
    return job


def retry_job(job: dict, *, admin_id: str, admin_name: str | None = None) -> dict:
    if job.get("state") not in {"publish_failed", "publish_released", "approved_for_publish"}:
        raise ValueError(f"cannot retry job from state {job.get('state')}")
    for step, value in list(job["progress"].items()):
        if value in {"failed", "running"}:
            job["progress"][step] = "pending"
    job["state"] = "approved_for_publish"
    job["error"] = None
    job["claimed_by_admin_id"] = None
    job["claimed_by_name"] = None
    job["claimed_at"] = None
    job["lease_expires_at"] = None
    job["last_heartbeat_at"] = None
    _append_history(job, "retry_requested", admin_id=admin_id, admin_name=admin_name)
    return job


def start_step(job: dict, step: str) -> dict:
    _ensure_nonterminal(job)
    _ensure_claimed(job)
    _ensure_prior_steps_complete(job, step)
    if _running_steps(job):
        raise ValueError("another publish step is already running")
    if job["progress"].get(step) != "pending":
        raise ValueError(f"cannot start step {step} from state {job['progress'].get(step)}")
    job["state"] = "publish_running"
    job["progress"][step] = "running"
    job.setdefault("step_timestamps", {}).setdefault(step, {})["started_at"] = iso_now()
    _append_history(job, "step_started", step=step)
    return job


def complete_step(job: dict, step: str, artifacts: dict | None = None) -> dict:
    if step not in STEP_ORDER:
        raise ValueError(f"unknown step: {step}")
    if job["progress"].get(step) != "running":
        raise ValueError(f"cannot complete step {step} from state {job['progress'].get(step)}")
    job["progress"][step] = "done"
    job["state"] = "publish_claimed"
    job.setdefault("step_timestamps", {}).setdefault(step, {})["completed_at"] = iso_now()
    if artifacts:
        job.setdefault("artifacts", {}).update({k: v for k, v in artifacts.items() if v})
    _append_history(job, "step_completed", step=step)
    return job


def skip_step(job: dict, step: str, reason: str | None = None) -> dict:
    _ensure_nonterminal(job)
    _ensure_claimed(job)
    _ensure_prior_steps_complete(job, step)
    if job["progress"].get(step) not in {"pending", "running"}:
        raise ValueError(f"cannot skip step {step} from state {job['progress'].get(step)}")
    for running in _running_steps(job):
        if running != step:
            raise ValueError("another publish step is already running")
    job["progress"][step] = "skipped"
    job["state"] = "publish_claimed"
    job.setdefault("step_timestamps", {}).setdefault(step, {})["completed_at"] = iso_now()
    _append_history(job, "step_skipped", step=step, reason=reason)
    return job


def fail_step(job: dict, step: str, error: str) -> dict:
    if step not in STEP_ORDER:
        raise ValueError(f"unknown step: {step}")
    if job["progress"].get(step) != "running":
        raise ValueError(f"cannot fail step {step} from state {job['progress'].get(step)}")
    job["progress"][step] = "failed"
    job.setdefault("step_timestamps", {}).setdefault(step, {})["failed_at"] = iso_now()
    job["state"] = "publish_failed"
    job["error"] = {"step": step, "message": error, "timestamp": iso_now()}
    _append_history(job, "step_failed", step=step, error=error)
    return job


def complete_job(job: dict) -> dict:
    if any(job["progress"].get(step) not in {"done", "skipped"} for step in STEP_ORDER):
        raise ValueError("cannot complete job before all publish steps are done or skipped")
    job["state"] = "publish_completed"
    job["lease_expires_at"] = None
    job["last_heartbeat_at"] = None
    _append_history(job, "completed")
    return job


def fail_job(job: dict, *, step: str, error: str) -> dict:
    return fail_step(job, step, error)


def claim_next_available(
    *,
    admin_id: str,
    admin_name: str | None = None,
    lease_seconds: int = 900,
    root: Path | None = None,
    store=None,
) -> dict | None:
    candidates = []
    for job in list_jobs(root=root, store=store):
        if job["state"] == "approved_for_publish":
            candidates.append(job)
        elif job["state"] == "publish_claimed" and job.get("claimed_by_admin_id") == admin_id:
            candidates.append(job)
        elif job["state"] == "publish_released":
            candidates.append(job)
        elif job["state"] == "publish_claimed" and not lease_is_active(job):
            candidates.append(job)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.get("created_at") or "", item["job_id"]))
    job = candidates[0]
    claim_job(job, admin_id=admin_id, admin_name=admin_name, lease_seconds=lease_seconds)
    save_job(job, root=root, store=store)
    return job
