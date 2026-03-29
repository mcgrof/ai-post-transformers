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


def test_episode_artifacts_use_published_episode_urls(monkeypatch, tmp_path):
    from scripts.publish_job_runner import _episode_artifacts

    podcasts_dir = tmp_path / "podcasts" / "episodes" / "turboquant-online-vector-quantization-with-near-optimal-distortion-rate"
    podcasts_dir.mkdir(parents=True)
    (podcasts_dir / "index.html").write_text("ok", encoding="utf-8")

    monkeypatch.setattr("scripts.publish_job_runner.ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.publish_job_runner._find_episode",
        lambda job: {
            "id": 103,
            "title": "Episode: TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate",
            "audio_file": str(tmp_path / "drafts/2026/03/2026-03-25-turboquant-online-vector-quantiz-1967b7.mp3"),
            "image_file": None,
            "description": "",
        },
    )

    artifacts = _episode_artifacts({"job_id": "pub_2026_03_26_211734"})

    assert artifacts["audio_url"] == "https://podcast.do-not-panic.com/episodes/2026-03-25-turboquant-online-vector-quantiz-1967b7.mp3"
    assert artifacts["srt_url"] == "https://podcast.do-not-panic.com/episodes/2026-03-25-turboquant-online-vector-quantiz-1967b7.srt"
    assert artifacts["page_url"] == "https://podcast.do-not-panic.com/episodes/turboquant-online-vector-quantization-with-near-optimal-distortion-rate/"


def test_verify_falls_back_to_remote_when_local_missing(monkeypatch, tmp_path):
    """When local audio/srt files are gone but remote URLs return 200,
    verification should pass — this is the TurboQuant-style scenario
    where publish completed but local draft cleanup removed the files."""
    from scripts.publish_job_runner import _verify_publish_success

    artifacts = {
        "audio_file": str(tmp_path / "nonexistent.mp3"),
        "srt_file": str(tmp_path / "nonexistent.srt"),
        "image_file": None,
        "audio_url": "https://podcast.do-not-panic.com/episodes/test.mp3",
        "srt_url": "https://podcast.do-not-panic.com/episodes/test.srt",
        "page_url": "https://podcast.do-not-panic.com/episodes/test/",
        "viz_url": None,
        "cover_url": None,
    }

    monkeypatch.setattr(
        "scripts.publish_job_runner._verify_remote_urls",
        lambda a: {
            "audio_url": 200,
            "srt_url": 200,
            "page_url": 200,
            "viz_url": "missing",
            "cover_url": "missing",
        },
    )

    result = _verify_publish_success(artifacts)
    assert result["ok"] is True
    assert result["remote"]["audio_url"] == 200


def test_verify_fails_when_audio_missing_everywhere(monkeypatch, tmp_path):
    """When audio is missing both locally and remotely, verify must fail."""
    from scripts.publish_job_runner import _verify_publish_success

    artifacts = {
        "audio_file": str(tmp_path / "nonexistent.mp3"),
        "srt_file": str(tmp_path / "nonexistent.srt"),
        "image_file": None,
        "audio_url": "https://podcast.do-not-panic.com/episodes/test.mp3",
        "srt_url": "https://podcast.do-not-panic.com/episodes/test.srt",
        "page_url": "https://podcast.do-not-panic.com/episodes/test/",
    }

    monkeypatch.setattr(
        "scripts.publish_job_runner._verify_remote_urls",
        lambda a: {
            "audio_url": 404,
            "srt_url": 200,
            "page_url": 200,
        },
    )

    result = _verify_publish_success(artifacts)
    assert result["ok"] is False


def test_verify_treats_viz_as_optional_by_default(monkeypatch, tmp_path):
    """Viz/infographic missing should not fail verify unless the job
    explicitly requires it."""
    from scripts.publish_job_runner import _verify_publish_success

    artifacts = {
        "audio_file": str(tmp_path / "exists.mp3"),
        "srt_file": str(tmp_path / "exists.srt"),
        "image_file": None,
        "audio_url": "https://podcast.do-not-panic.com/episodes/test.mp3",
        "srt_url": "https://podcast.do-not-panic.com/episodes/test.srt",
        "page_url": "https://podcast.do-not-panic.com/episodes/test/",
        "viz_url": None,
        "cover_url": None,
    }

    # Create the local files so local check passes
    (tmp_path / "exists.mp3").write_bytes(b"audio")
    (tmp_path / "exists.srt").write_bytes(b"srt")

    result = _verify_publish_success(
        artifacts, requirements={"viz": False, "cover": False},
    )
    assert result["ok"] is True


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
