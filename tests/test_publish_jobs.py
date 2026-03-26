import pytest

from scripts.publish_jobs import (
    STEP_ORDER,
    claim_job,
    complete_job,
    complete_step,
    fail_job,
    load_job,
    make_job_record,
    release_job,
    retry_job,
    save_job,
    start_step,
)


def test_publish_job_lifecycle(tmp_path):
    job = make_job_record(
        draft_key="drafts/2026/03/example-abc123.mp3",
        title="Example Episode",
        episode_id=42,
        approved_by_admin_id="admin-1",
        approved_by_name="mcgrof",
    )
    claim_job(job, admin_id="admin-1", admin_name="mcgrof")
    for step in STEP_ORDER:
        start_step(job, step)
        artifacts = {"audio_url": "https://example.test/audio.mp3"} if step == "publish" else None
        complete_step(job, step, artifacts)
    complete_job(job)

    path = save_job(job, root=tmp_path)
    loaded = load_job(path)

    assert loaded["state"] == "publish_completed"
    assert loaded["progress"]["publish"] == "done"
    assert loaded["artifacts"]["audio_url"] == "https://example.test/audio.mp3"


def test_publish_job_retry_resets_failed_step():
    job = make_job_record(draft_key="drafts/2026/03/example-abc123.mp3")
    claim_job(job, admin_id="admin-1", admin_name="mcgrof")
    start_step(job, "publish")
    fail_job(job, step="publish", error="command failed")
    release_job(job, admin_id="admin-1", reason="manual release")
    retry_job(job, admin_id="admin-1", admin_name="mcgrof")

    assert job["state"] == "approved_for_publish"
    assert job["progress"]["publish"] == "pending"
    assert job["error"] is None


def test_release_resets_running_step_to_pending():
    job = make_job_record(draft_key="drafts/2026/03/example-abc123.mp3")
    claim_job(job, admin_id="admin-1", admin_name="mcgrof")
    start_step(job, "publish")

    release_job(job, admin_id="admin-1", reason="pause")

    assert job["state"] == "publish_released"
    assert job["progress"]["publish"] == "pending"
    assert job["claimed_by_admin_id"] is None


@pytest.mark.parametrize("step", ["viz", "cover", "site", "verify"])
def test_step_order_enforced(step):
    job = make_job_record(draft_key="drafts/2026/03/example-abc123.mp3")
    claim_job(job, admin_id="admin-1", admin_name="mcgrof")

    with pytest.raises(ValueError):
        start_step(job, step)
