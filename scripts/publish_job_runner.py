#!/usr/bin/env python3
"""Run the publish pipeline for a single durable publish job."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import get_connection, init_db, list_podcasts
from scripts.publish_job_store import get_publish_job_store
from scripts.publish_jobs import (
    complete_job,
    complete_step,
    fail_job,
    heartbeat_job,
    load_job,
    save_job,
    save_result,
    start_step,
)

PODCAST_DOMAIN = "https://podcast.do-not-panic.com"


def _run_shell(command: str, *, cwd: Path = ROOT) -> None:
    wrapped = f"source ~/.enhance-bash >/dev/null 2>&1 && {command}"
    subprocess.run(
        ["bash", "-lc", wrapped],
        cwd=str(cwd),
        check=True,
    )


def _find_episode(job: dict) -> dict | None:
    conn = get_connection()
    init_db(conn)
    try:
        for episode in list_podcasts(conn):
            if job.get("episode_id") and episode.get("id") == job["episode_id"]:
                return episode
            audio = episode.get("audio_file") or ""
            if job.get("draft_stem") and Path(job["draft_stem"]).name in audio:
                return episode
    finally:
        conn.close()
    return None


def _publish_draft_stem(job: dict, episode: dict | None = None) -> str:
    episode = episode or _find_episode(job)
    audio = (episode or {}).get("audio_file") or ""
    if audio:
        return os.path.splitext(audio)[0]
    draft_key = job.get("draft_key") or ""
    if draft_key:
        return os.path.splitext(draft_key)[0]
    return job["draft_stem"]


def _url_for_public_path(path: str | None) -> str | None:
    if not path:
        return None
    rel = path.replace(str(ROOT) + os.sep, "")
    rel = rel.lstrip("./")
    return f"{PODCAST_DOMAIN}/{rel}"


def _resolve_local_path(path: str | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def _episode_artifacts(job: dict) -> dict:
    episode = _find_episode(job)
    if not episode:
        raise RuntimeError(f"unable to find episode for job {job['job_id']}")

    audio_file = episode.get("audio_file")
    image_file = episode.get("image_file")
    stem = os.path.splitext(audio_file)[0] if audio_file else None
    srt_file = f"{stem}.srt" if stem else None
    page_url = None
    if audio_file:
        slug = os.path.splitext(os.path.basename(audio_file))[0]
        page_url = f"{PODCAST_DOMAIN}/episodes/{slug}/"

    artifacts = {
        "audio_file": audio_file,
        "image_file": image_file,
        "srt_file": srt_file,
        "audio_url": _url_for_public_path(audio_file),
        "cover_url": _url_for_public_path(image_file),
        "srt_url": _url_for_public_path(srt_file),
        "page_url": page_url,
        "viz_url": _extract_viz_url(episode.get("description") or ""),
        "thumb_url": _thumbnail_url(audio_file),
        "episode_id": episode.get("id"),
    }
    return artifacts


def _thumbnail_url(audio_file: str | None) -> str | None:
    if not audio_file:
        return None
    stem = os.path.splitext(os.path.basename(audio_file))[0]
    year_month = Path(audio_file).parts[-3:-1]
    if len(year_month) != 2:
        return None
    return f"{PODCAST_DOMAIN}/thumbs/{year_month[0]}-{year_month[1]}-{stem}.webp"


def _extract_viz_url(description: str) -> str | None:
    marker = f"{PODCAST_DOMAIN}/viz/"
    for token in description.split():
        if token.startswith(marker):
            return token.rstrip(").,]")
    return None


def _verify_local_artifacts(artifacts: dict) -> dict:
    audio_file = _resolve_local_path(artifacts.get("audio_file"))
    srt_file = _resolve_local_path(artifacts.get("srt_file"))
    image_file = _resolve_local_path(artifacts.get("image_file"))
    checks = {
        "audio_file_exists": bool(audio_file and audio_file.exists()),
        "srt_file_exists": bool(srt_file and srt_file.exists()),
        "image_file_exists": bool(not image_file or image_file.exists()),
        "audio_url_present": bool(artifacts.get("audio_url")),
        "page_url_present": bool(artifacts.get("page_url")),
    }
    checks["ok"] = all(checks.values())
    return checks


def _verify_remote_urls(artifacts: dict) -> dict:
    results = {}
    for key in ("audio_url", "srt_url", "page_url", "viz_url", "cover_url"):
        url = artifacts.get(key)
        if not url:
            results[key] = "missing"
            continue
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "publish-job-runner/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                results[key] = resp.status
        except urllib.error.HTTPError as exc:
            results[key] = exc.code
        except Exception as exc:  # pragma: no cover - network dependent
            results[key] = f"error: {exc}"
    return results


def _resolve_publish_draft(job: dict) -> str:
    episode = _find_episode(job)
    if episode and episode.get("audio_file"):
        return os.path.splitext(episode["audio_file"])[0]
    if job.get("draft_key"):
        return os.path.splitext(job["draft_key"])[0]
    return job["draft_stem"]


def _update_job_with_heartbeat(
    job: dict,
    *,
    admin_id: str,
    lease_seconds: int,
    store,
) -> None:
    heartbeat_job(job, admin_id=admin_id, lease_seconds=lease_seconds)
    save_job(job, store=store)


def process_job(
    job_path: str | Path,
    *,
    admin_id: str,
    admin_name: str | None = None,
    lease_seconds: int = 900,
    verify_remote: bool = False,
    store=None,
) -> dict:
    store = store or get_publish_job_store()
    job = load_job(job_path, store=store)
    if job.get("claimed_by_admin_id") and job["claimed_by_admin_id"] != admin_id:
        raise RuntimeError(
            f"job {job['job_id']} is claimed by {job.get('claimed_by_admin_id')}"
        )

    try:
        start_step(job, "publish")
        save_job(job, store=store)
        publish_draft = _resolve_publish_draft(job)
        _run_shell(f".venv/bin/python gen-podcast.py publish --draft '{publish_draft}'")
        complete_step(job, "publish", _episode_artifacts(job))
        _update_job_with_heartbeat(
            job, admin_id=admin_id, lease_seconds=lease_seconds, store=store
        )

        if job["requirements"].get("viz", True):
            start_step(job, "viz")
            save_job(job, store=store)
            artifacts = _episode_artifacts(job)
            public_stem = os.path.splitext(artifacts["audio_file"])[0]
            _run_shell(f".venv/bin/python gen-podcast.py gen-viz --draft '{public_stem}'")
            complete_step(job, "viz", _episode_artifacts(job))
            _update_job_with_heartbeat(
                job, admin_id=admin_id, lease_seconds=lease_seconds, store=store
            )

        else:
            complete_step(job, "viz")
            save_job(job, store=store)

        if job["requirements"].get("cover", True):
            start_step(job, "cover")
            save_job(job, store=store)
            artifacts = _episode_artifacts(job)
            episode_id = artifacts.get("episode_id") or job.get("episode_id")
            if episode_id:
                _run_shell(f".venv/bin/python backfill_images.py --episode-id {episode_id}")
            complete_step(job, "cover", _episode_artifacts(job))
            _update_job_with_heartbeat(
                job, admin_id=admin_id, lease_seconds=lease_seconds, store=store
            )
        else:
            complete_step(job, "cover")
            save_job(job, store=store)

        if job["requirements"].get("publish_site", True):
            start_step(job, "site")
            save_job(job, store=store)
            _run_shell("make publish-site")
            complete_step(job, "site", _episode_artifacts(job))
            _update_job_with_heartbeat(
                job, admin_id=admin_id, lease_seconds=lease_seconds, store=store
            )
        else:
            complete_step(job, "site")
            save_job(job, store=store)

        start_step(job, "verify")
        save_job(job, store=store)
        artifacts = _episode_artifacts(job)
        verification = {
            "local": _verify_local_artifacts(artifacts),
        }
        if verify_remote:
            verification["remote"] = _verify_remote_urls(artifacts)
        if not verification["local"]["ok"]:
            raise RuntimeError(f"artifact verification failed: {verification['local']}")
        complete_step(job, "verify", artifacts)
        complete_job(job)
        save_job(job, store=store)
        save_result(job, verification=verification, store=store)
        return job
    except subprocess.CalledProcessError as exc:
        fail_job(job, step=_current_running_step(job), error=str(exc))
        save_job(job, store=store)
        save_result(job, store=store)
        raise
    except Exception as exc:
        fail_job(job, step=_current_running_step(job), error=str(exc))
        save_job(job, store=store)
        save_result(job, store=store)
        raise


def _current_running_step(job: dict) -> str:
    for step in ("verify", "site", "cover", "viz", "publish"):
        if job.get("progress", {}).get(step) == "running":
            return step
    return "publish"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one durable publish job.")
    parser.add_argument("--job", required=True, help="path to publish job JSON")
    parser.add_argument("--admin-id", required=True)
    parser.add_argument("--admin-name")
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument(
        "--store",
        choices=("auto", "local", "r2"),
        default="auto",
        help="publish job record backend",
    )
    parser.add_argument(
        "--local-root",
        help="local publish job root when using the filesystem fallback",
    )
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="also probe remote URLs with HEAD requests",
    )
    args = parser.parse_args()

    store = get_publish_job_store(
        mode=args.store,
        root=Path(args.local_root) if args.local_root else None,
    )

    process_job(
        args.job,
        admin_id=args.admin_id,
        admin_name=args.admin_name,
        lease_seconds=args.lease_seconds,
        verify_remote=args.verify_remote,
        store=store,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
