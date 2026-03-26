import subprocess
from pathlib import Path

from scripts.publish_job_runner import process_job
from scripts.publish_job_store import LocalPublishJobStore
from scripts.publish_jobs import claim_job, load_job, make_job_record, save_job


class ArtifactSequence:
    def __init__(self):
        self.calls = 0

    def __call__(self, job):
        self.calls += 1
        return {
            "audio_file": "public/2026/03/example.mp3",
            "image_file": "public/2026/03/example.png",
            "srt_file": "public/2026/03/example.srt",
            "audio_url": "https://podcast.do-not-panic.com/public/2026/03/example.mp3",
            "cover_url": "https://podcast.do-not-panic.com/public/2026/03/example.png",
            "srt_url": "https://podcast.do-not-panic.com/public/2026/03/example.srt",
            "page_url": "https://podcast.do-not-panic.com/episodes/example/",
            "viz_url": "https://podcast.do-not-panic.com/viz/example.html",
            "thumb_url": "https://podcast.do-not-panic.com/thumbs/2026-03-example.webp",
            "episode_id": 42,
        }


def _seed_job(tmp_path):
    store = LocalPublishJobStore(root=tmp_path)
    job = make_job_record(
        draft_key="drafts/2026/03/example.mp3",
        title="Example Episode",
        episode_id=42,
    )
    claim_job(job, admin_id="admin-1", admin_name="mcgrof")
    path = save_job(job, store=store)
    return store, path, job


def test_process_job_runs_publish_pipeline_in_order(monkeypatch, tmp_path):
    store, path, _ = _seed_job(tmp_path)
    commands = []
    artifacts = ArtifactSequence()

    monkeypatch.setattr("scripts.publish_job_runner._run_shell", lambda command, cwd=Path('.'): commands.append(command))
    monkeypatch.setattr("scripts.publish_job_runner._episode_artifacts", artifacts)
    monkeypatch.setattr("scripts.publish_job_runner._verify_local_artifacts", lambda artifacts: {"ok": True})

    finished = process_job(
        path,
        admin_id="admin-1",
        admin_name="mcgrof",
        store=store,
    )

    assert finished["state"] == "publish_completed"
    assert commands == [
        ".venv/bin/python gen-podcast.py publish --draft 'drafts/2026/03/example'",
        ".venv/bin/python gen-podcast.py gen-viz --draft 'public/2026/03/example'",
        ".venv/bin/python backfill_images.py --episode-id 42",
        "make publish-site",
    ]

    loaded = load_job(path, store=store)
    assert loaded["progress"] == {
        "publish": "done",
        "viz": "done",
        "cover": "done",
        "site": "done",
        "verify": "done",
    }
    assert loaded["artifacts"]["viz_url"] == "https://podcast.do-not-panic.com/viz/example.html"


def test_process_job_marks_failed_step_when_command_fails(monkeypatch, tmp_path):
    store, path, _ = _seed_job(tmp_path)
    artifacts = ArtifactSequence()
    commands = []

    def fake_run(command, cwd=Path('.')):
        commands.append(command)
        if "gen-viz" in command:
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

    monkeypatch.setattr("scripts.publish_job_runner._run_shell", fake_run)
    monkeypatch.setattr("scripts.publish_job_runner._episode_artifacts", artifacts)
    monkeypatch.setattr("scripts.publish_job_runner._verify_local_artifacts", lambda artifacts: {"ok": True})

    try:
        process_job(path, admin_id="admin-1", admin_name="mcgrof", store=store)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("expected CalledProcessError")

    loaded = load_job(path, store=store)
    assert loaded["state"] == "publish_failed"
    assert loaded["progress"]["publish"] == "done"
    assert loaded["progress"]["viz"] == "failed"
    assert loaded["error"]["step"] == "viz"
