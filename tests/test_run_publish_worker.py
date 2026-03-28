from scripts.publish_job_store import LocalPublishJobStore
from scripts.publish_jobs import (
    claim_job,
    load_job,
    make_job_record,
    save_job,
    start_step,
)
from scripts.run_publish_worker import run_once


def test_run_once_skips_new_claim_when_admin_has_active_running_job(
    monkeypatch,
    tmp_path,
):
    store = LocalPublishJobStore(root=tmp_path)

    running = make_job_record(
        draft_key="drafts/2026/03/running.mp3",
        job_id="pub_2026_03_28_130000",
        created_at="2026-03-28T13:00:00+00:00",
    )
    claim_job(running, admin_id="admin-1", admin_name="mcgrof")
    start_step(running, "publish")
    save_job(running, store=store)

    pending = make_job_record(
        draft_key="drafts/2026/03/pending.mp3",
        job_id="pub_2026_03_28_130100",
        created_at="2026-03-28T13:01:00+00:00",
    )
    save_job(pending, store=store)

    calls = []
    monkeypatch.setattr(
        "scripts.run_publish_worker.process_job",
        lambda job_path, **kwargs: calls.append(job_path),
    )

    processed = run_once(
        "admin-1",
        "mcgrof",
        900,
        False,
        store=store,
    )

    assert processed == 0
    assert calls == []
    assert load_job(pending["job_id"], store=store)["state"] == "approved_for_publish"


def test_run_once_releases_stale_running_job_before_reclaiming(
    monkeypatch,
    tmp_path,
):
    store = LocalPublishJobStore(root=tmp_path)

    stale = make_job_record(
        draft_key="drafts/2026/03/stale.mp3",
        job_id="pub_2026_03_28_120000",
        created_at="2026-03-28T12:00:00+00:00",
    )
    claim_job(stale, admin_id="admin-1", admin_name="mcgrof")
    start_step(stale, "publish")
    stale["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    save_job(stale, store=store)

    calls = []
    monkeypatch.setattr(
        "scripts.run_publish_worker.process_job",
        lambda job_path, **kwargs: calls.append(job_path),
    )

    processed = run_once(
        "admin-1",
        "mcgrof",
        900,
        False,
        store=store,
    )

    assert processed == 1
    assert calls == [stale["job_id"]]

    loaded = load_job(stale["job_id"], store=store)
    assert loaded["state"] == "publish_claimed"
    assert loaded["progress"]["publish"] == "pending"
    assert loaded["claimed_by_admin_id"] == "admin-1"
    assert any(entry["action"] == "released" for entry in loaded["history"])
