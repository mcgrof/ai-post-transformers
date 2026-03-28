import subprocess

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

    monkeypatch.setattr(
        "scripts.publish_job_runner._run_shell_with_heartbeat",
        lambda command, **kwargs: commands.append(command),
    )
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

    def fake_run(command, **kwargs):
        commands.append(command)
        if "gen-viz" in command:
            raise subprocess.CalledProcessError(returncode=1, cmd=command)

    monkeypatch.setattr("scripts.publish_job_runner._run_shell_with_heartbeat", fake_run)
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


def test_process_job_resolves_legacy_draft_stem_from_episode_audio(monkeypatch, tmp_path):
    store = LocalPublishJobStore(root=tmp_path)
    job = make_job_record(
        draft_key="drafts/ep110.mp3",
        title="Splitwise: Phase-Split LLM Inference",
        episode_id=110,
    )
    claim_job(job, admin_id="admin-1", admin_name="mcgrof")
    path = save_job(job, store=store)

    commands = []
    artifacts = ArtifactSequence()

    monkeypatch.setattr(
        "scripts.publish_job_runner._run_shell_with_heartbeat",
        lambda command, **kwargs: commands.append(command),
    )
    monkeypatch.setattr("scripts.publish_job_runner._episode_artifacts", artifacts)
    monkeypatch.setattr("scripts.publish_job_runner._verify_local_artifacts", lambda artifacts: {"ok": True})
    monkeypatch.setattr(
        "scripts.publish_job_runner._find_episode",
        lambda job: {
            "id": 110,
            "audio_file": "drafts/2026/03/2026-03-26-splitwise-phase-split-llm-inference-e8945b.mp3",
        },
    )

    finished = process_job(path, admin_id="admin-1", admin_name="mcgrof", store=store)

    assert finished["state"] == "publish_completed"
    assert commands[0] == ".venv/bin/python gen-podcast.py publish --draft 'drafts/2026/03/2026-03-26-splitwise-phase-split-llm-inference-e8945b'"


def test_process_job_skips_duplicate_running_invocation(monkeypatch, tmp_path):
    store, path, job = _seed_job(tmp_path)
    job = load_job(path, store=store)
    job["state"] = "publish_running"
    job["progress"]["publish"] = "running"
    save_job(job, store=store)

    monkeypatch.setattr(
        "scripts.publish_job_runner._run_shell_with_heartbeat",
        lambda command, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate invocation should not run commands")
        ),
    )

    returned = process_job(path, admin_id="admin-1", admin_name="mcgrof", store=store)

    assert returned["state"] == "publish_running"
    loaded = load_job(path, store=store)
    assert loaded["progress"]["publish"] == "running"


def test_run_shell_with_heartbeat_renews_lease_during_long_step(monkeypatch):
    updates = []

    class FakeProc:
        def __init__(self):
            self.calls = 0

        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return 0

    monkeypatch.setattr(
        "scripts.publish_job_runner.subprocess.Popen",
        lambda *args, **kwargs: FakeProc(),
    )
    monkeypatch.setattr(
        "scripts.publish_job_runner._update_job_with_heartbeat",
        lambda job, **kwargs: updates.append(job["job_id"]),
    )

    from scripts.publish_job_runner import _run_shell_with_heartbeat

    _run_shell_with_heartbeat(
        "true",
        job={"job_id": "pub_2026_03_28_130000"},
        admin_id="admin-1",
        lease_seconds=900,
        store=object(),
        heartbeat_interval=1,
    )

    assert updates == ["pub_2026_03_28_130000"]
