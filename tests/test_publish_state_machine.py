import copy
from collections import deque

import pytest

from scripts.publish_jobs import (
    STEP_ORDER,
    claim_job,
    complete_job,
    complete_step,
    fail_job,
    heartbeat_job,
    make_job_record,
    release_job,
    retry_job,
    skip_step,
    start_step,
)


def _canonical(job):
    return (
        job["state"],
        job.get("claimed_by_admin_id"),
        tuple((step, job["progress"][step]) for step in STEP_ORDER),
        bool(job.get("error")),
        bool(job.get("lease_expires_at")),
    )


def _running_step(job):
    for step in STEP_ORDER:
        if job["progress"][step] == "running":
            return step
    return None


def _next_pending_step(job):
    for step in STEP_ORDER:
        if job["progress"][step] == "pending":
            return step
    return None


def _assert_invariants(job):
    state = job["state"]
    running = [step for step in STEP_ORDER if job["progress"][step] == "running"]
    failed = [step for step in STEP_ORDER if job["progress"][step] == "failed"]

    assert len(running) <= 1

    if state == "approved_for_publish":
        assert job.get("claimed_by_admin_id") is None
        assert not running
        assert not job.get("lease_expires_at")
        assert job.get("error") is None

    if state == "publish_claimed":
        assert job.get("claimed_by_admin_id") is not None
        assert job.get("lease_expires_at") is not None
        assert not running

    if state == "publish_running":
        assert job.get("claimed_by_admin_id") is not None
        assert job.get("lease_expires_at") is not None
        assert len(running) == 1

    if state == "publish_released":
        assert job.get("claimed_by_admin_id") is None
        assert not job.get("lease_expires_at")
        assert not running

    if state == "publish_failed":
        assert job.get("error") is not None
        assert len(failed) == 1
        assert not running

    if state == "publish_completed":
        assert all(job["progress"][step] in {"done", "skipped"} for step in STEP_ORDER)
        assert not running
        assert not failed


ACTIONS = {
    "claim_a": lambda job: claim_job(job, admin_id="admin-a", admin_name="A"),
    "claim_b": lambda job: claim_job(job, admin_id="admin-b", admin_name="B"),
    "heartbeat_a": lambda job: heartbeat_job(job, admin_id="admin-a"),
    "heartbeat_b": lambda job: heartbeat_job(job, admin_id="admin-b"),
    "release_a": lambda job: release_job(job, admin_id="admin-a", reason="pause"),
    "release_b": lambda job: release_job(job, admin_id="admin-b", reason="pause"),
    "retry_a": lambda job: retry_job(job, admin_id="admin-a", admin_name="A"),
    "retry_b": lambda job: retry_job(job, admin_id="admin-b", admin_name="B"),
    "start_next": lambda job: start_step(job, _next_pending_step(job)),
    "complete_current": lambda job: complete_step(job, _running_step(job)),
    "fail_current": lambda job: fail_job(job, step=_running_step(job), error="boom"),
    "skip_next": lambda job: skip_step(job, _next_pending_step(job), reason="not required"),
    "complete_job": lambda job: complete_job(job),
}


def _apply(job, action):
    candidate = copy.deepcopy(job)
    fn = ACTIONS[action]
    if action in {"start_next", "skip_next"} and _next_pending_step(candidate) is None:
        raise ValueError("no pending step")
    if action in {"complete_current", "fail_current"} and _running_step(candidate) is None:
        raise ValueError("no running step")
    return fn(candidate)


def test_publish_state_machine_reachable_states_satisfy_invariants():
    start = make_job_record(draft_key="drafts/2026/03/example.mp3")
    seen = {_canonical(start)}
    queue = deque([start])
    reached_states = {start["state"]}

    while queue:
        job = queue.popleft()
        _assert_invariants(job)
        for action in ACTIONS:
            try:
                nxt = _apply(job, action)
            except ValueError:
                continue
            _assert_invariants(nxt)
            reached_states.add(nxt["state"])
            key = _canonical(nxt)
            if key not in seen:
                seen.add(key)
                queue.append(nxt)

    assert "approved_for_publish" in reached_states
    assert "publish_claimed" in reached_states
    assert "publish_running" in reached_states
    assert "publish_released" in reached_states
    assert "publish_failed" in reached_states
    assert "publish_completed" in reached_states


@pytest.mark.parametrize(
    "actions,expected_state",
    [
        (["claim_a", "start_next", "complete_current", "start_next", "complete_current", "start_next", "complete_current", "start_next", "complete_current", "start_next", "complete_current", "complete_job"], "publish_completed"),
        (["claim_a", "start_next", "fail_current"], "publish_failed"),
        (["claim_a", "start_next", "release_a", "retry_a"], "approved_for_publish"),
    ],
)
def test_publish_state_machine_reference_paths(actions, expected_state):
    job = make_job_record(draft_key="drafts/2026/03/example.mp3")
    for action in actions:
        job = _apply(job, action)
    assert job["state"] == expected_state
    _assert_invariants(job)
