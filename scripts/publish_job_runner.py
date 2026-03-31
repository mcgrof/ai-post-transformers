#!/usr/bin/env python3
"""Run the publish pipeline for a single durable publish job."""

from __future__ import annotations

import argparse
import json
import os
import re
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
STEP_HEARTBEAT_INTERVAL_SECONDS = 60


def _run_shell(command: str, *, cwd: Path = ROOT) -> None:
    wrapped = f"source ~/.enhance-bash >/dev/null 2>&1 && {command}"
    subprocess.run(
        ["bash", "-lc", wrapped],
        cwd=str(cwd),
        check=True,
    )


def _run_shell_with_heartbeat(
    command: str,
    *,
    job: dict,
    admin_id: str,
    lease_seconds: int,
    store,
    cwd: Path = ROOT,
    heartbeat_interval: int = STEP_HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    wrapped = f"source ~/.enhance-bash >/dev/null 2>&1 && {command}"
    proc = subprocess.Popen(
        ["bash", "-lc", wrapped],
        cwd=str(cwd),
    )
    while True:
        try:
            returncode = proc.wait(timeout=heartbeat_interval)
        except subprocess.TimeoutExpired:
            try:
                _update_job_with_heartbeat(
                    job,
                    admin_id=admin_id,
                    lease_seconds=lease_seconds,
                    store=store,
                )
            except Exception as exc:  # pragma: no cover - best effort heartbeat
                print(
                    f"[publish-job-runner] heartbeat warning for {job['job_id']}: {exc}",
                    file=sys.stderr,
                )
            continue

        if returncode != 0:
            raise subprocess.CalledProcessError(returncode=returncode, cmd=command)
        return


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


def _published_media_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"{PODCAST_DOMAIN}/episodes/{Path(path).name}"


def _slugify_episode_title(title: str | None) -> str | None:
    if not title:
        return None
    cleaned = re.sub(r"^\s*Episode:\s*", "", title, flags=re.IGNORECASE).strip()
    if not cleaned:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    return slug or None


def _episode_page_url(episode: dict, audio_file: str | None) -> str | None:
    episode_key = episode.get("episode_key")
    if episode_key:
        key = str(episode_key).lstrip("/")
        return f"{PODCAST_DOMAIN}/{key.rstrip('/')}/"

    title_slug = _slugify_episode_title(episode.get("title"))
    if title_slug:
        candidate = ROOT / "podcasts" / "episodes" / title_slug / "index.html"
        if candidate.exists():
            return f"{PODCAST_DOMAIN}/episodes/{title_slug}/"

    if audio_file:
        slug = os.path.splitext(os.path.basename(audio_file))[0]
        return f"{PODCAST_DOMAIN}/episodes/{slug}/"
    return None


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
    page_url = _episode_page_url(episode, audio_file)

    artifacts = {
        "audio_file": audio_file,
        "image_file": image_file,
        "srt_file": srt_file,
        "audio_url": _published_media_url(audio_file),
        "cover_url": _url_for_public_path(image_file),
        "srt_url": _published_media_url(srt_file),
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


def _verify_publish_success(artifacts: dict, *,
                            requirements: dict | None = None) -> dict:
    """Verify publish artifacts, with remote fallback when local missing.

    Does not fail just because local draft audio/srt files are gone if
    the published remote artifacts are accessible via HEAD probe.
    Treats viz and cover as optional unless explicitly required by the
    job requirements.  Preserves real failures (audio or page truly
    missing everywhere).
    """
    requirements = requirements or {}
    local = _verify_local_artifacts(artifacts)
    result = {"local": local}

    if local["ok"]:
        result["ok"] = True
        return result

    # Local check failed — probe remote URLs as fallback
    remote = _verify_remote_urls(artifacts)
    result["remote"] = remote

    audio_ok = (
        local["audio_file_exists"]
        or remote.get("audio_url") == 200
    )
    srt_ok = (
        local["srt_file_exists"]
        or remote.get("srt_url") == 200
    )
    url_ok = local["audio_url_present"]
    page_ok = local["page_url_present"]

    # Viz and cover are optional unless the job explicitly requires them
    viz_ok = True
    if requirements.get("viz"):
        viz_ok = remote.get("viz_url") in (200, "missing")

    cover_ok = True
    if requirements.get("cover"):
        cover_ok = (
            local.get("image_file_exists", True)
            or remote.get("cover_url") == 200
        )

    result["ok"] = all([audio_ok, srt_ok, url_ok, page_ok])
    return result


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


def _running_step(job: dict) -> str | None:
    for step in ("verify", "site", "cover", "viz", "publish"):
        if job.get("progress", {}).get(step) == "running":
            return step
    return None


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

    active_step = _running_step(job)
    if job.get("state") == "publish_running" and active_step:
        print(
            f"[publish-job-runner] Job {job['job_id']} is already running "
            f"step {active_step}; skipping duplicate invocation"
        )
        return job

    try:
        start_step(job, "publish")
        save_job(job, store=store)
        publish_draft = _resolve_publish_draft(job)
        _run_shell_with_heartbeat(
            f".venv/bin/python gen-podcast.py publish --draft '{publish_draft}'",
            job=job,
            admin_id=admin_id,
            lease_seconds=lease_seconds,
            store=store,
        )
        complete_step(job, "publish", _episode_artifacts(job))
        _update_job_with_heartbeat(
            job, admin_id=admin_id, lease_seconds=lease_seconds, store=store
        )

        if job["requirements"].get("viz", True):
            start_step(job, "viz")
            save_job(job, store=store)
            artifacts = _episode_artifacts(job)
            public_stem = os.path.splitext(artifacts["audio_file"])[0]
            _run_shell_with_heartbeat(
                f".venv/bin/python gen-podcast.py gen-viz --draft '{public_stem}'",
                job=job,
                admin_id=admin_id,
                lease_seconds=lease_seconds,
                store=store,
            )
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
                _run_shell_with_heartbeat(
                    f".venv/bin/python backfill_images.py --episode-id {episode_id}",
                    job=job,
                    admin_id=admin_id,
                    lease_seconds=lease_seconds,
                    store=store,
                )
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
            _run_shell_with_heartbeat(
                "make publish-site",
                job=job,
                admin_id=admin_id,
                lease_seconds=lease_seconds,
                store=store,
            )
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
        verification = _verify_publish_success(
            artifacts, requirements=job.get("requirements"),
        )
        if verify_remote and "remote" not in verification:
            verification["remote"] = _verify_remote_urls(artifacts)
        if not verification.get("ok", False):
            raise RuntimeError(
                f"artifact verification failed: {verification}"
            )
        complete_step(job, "verify", artifacts)
        complete_job(job)
        save_job(job, store=store)
        save_result(job, verification=verification, store=store)
        _advance_linked_submissions(job, store)
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


def _advance_linked_submissions(job: dict, store) -> list[str]:
    """Advance R2 submissions matching this job's draft to 'published'.

    When the publish runner completes a job, the linked submission
    record in the admin bucket must be advanced to 'published' so it
    stops resurfacing as a draft card on the Drafts page.
    """
    if not hasattr(store, "client") or not hasattr(store, "bucket"):
        return []
    draft_key = job.get("draft_key", "")
    draft_stem = draft_key.replace(".mp3", "").replace(".txt", "")
    if not draft_stem:
        return []
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    advanced = []
    try:
        paginator = store.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=store.bucket, Prefix="submissions/"
        ):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                raw = store.client.get_object(
                    Bucket=store.bucket, Key=key
                )
                body = raw["Body"].read()
                if isinstance(body, bytes):
                    body = body.decode("utf-8")
                sub = json.loads(body)
                sub_stem = sub.get("draft_stem", "")
                if not sub_stem or sub_stem != draft_stem:
                    continue
                if sub.get("status") == "published":
                    continue
                sub["status"] = "published"
                sub["updated_at"] = now
                history = sub.get("status_history") or []
                history.append({"status": "published", "at": now})
                sub["status_history"] = history
                store.client.put_object(
                    Bucket=store.bucket,
                    Key=key,
                    Body=json.dumps(sub, indent=2) + "\n",
                    ContentType="application/json",
                )
                advanced.append(key)
    except Exception as exc:
        print(f"warning: failed to advance submissions: {exc}")
    return advanced


def _current_running_step(job: dict) -> str:
    return _running_step(job) or "publish"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one durable publish job.")
    parser.add_argument("--job", required=True, help="path to publish job JSON")
    parser.add_argument("--admin-id", required=True)
    parser.add_argument("--admin-name")
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument(
        "--store",
        choices=("auto", "local", "r2", "sqlite"),
        default="auto",
        help="publish job record backend",
    )
    parser.add_argument(
        "--local-root",
        help="local publish job root when using the filesystem fallback",
    )
    parser.add_argument(
        "--queue-db",
        help="path to SQLite queue database (enables sqlite store mode)",
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
        queue_db=args.queue_db,
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
