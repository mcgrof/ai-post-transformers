#!/usr/bin/env python3
"""Claim and process durable publish jobs for one admin."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.publish_job_runner import process_job
from scripts.publish_job_store import get_publish_job_store
from scripts.publish_jobs import claim_next_available, list_jobs


def _claimed_jobs_for_admin(admin_id: str, *, store) -> list[dict]:
    jobs = []
    for job in list_jobs(store=store):
        if job.get("claimed_by_admin_id") == admin_id and job.get("state") in {
            "publish_claimed",
            "publish_running",
        }:
            jobs.append(job)
    jobs.sort(key=lambda item: (item.get("created_at") or "", item["job_id"]))
    return jobs


def _process_claimed(
    admin_id: str,
    admin_name: str | None,
    lease_seconds: int,
    verify_remote: bool,
    *,
    store,
) -> int:
    processed = 0
    for job in _claimed_jobs_for_admin(admin_id, store=store):
        process_job(
            job_path=job["job_id"],
            admin_id=admin_id,
            admin_name=admin_name,
            lease_seconds=lease_seconds,
            verify_remote=verify_remote,
            store=store,
        )
        processed += 1
    return processed


def run_once(
    admin_id: str,
    admin_name: str | None,
    lease_seconds: int,
    verify_remote: bool,
    *,
    store,
) -> int:
    processed = _process_claimed(
        admin_id, admin_name, lease_seconds, verify_remote, store=store
    )
    if processed:
        return processed
    job = claim_next_available(
        admin_id=admin_id,
        admin_name=admin_name,
        lease_seconds=lease_seconds,
        store=store,
    )
    if not job:
        return 0
    return _process_claimed(
        admin_id, admin_name, lease_seconds, verify_remote, store=store
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the durable publish worker.")
    parser.add_argument("--admin-id", required=True)
    parser.add_argument("--admin-name")
    parser.add_argument("--claim-next", action="store_true")
    parser.add_argument("--process-claimed", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument("--verify-remote", action="store_true")
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
    args = parser.parse_args()

    store = get_publish_job_store(
        mode=args.store,
        root=Path(args.local_root) if args.local_root else None,
    )

    if not any((args.claim_next, args.process_claimed, args.once, args.loop)):
        parser.error("choose one of --claim-next, --process-claimed, --once, or --loop")

    if args.claim_next:
        job = claim_next_available(
            admin_id=args.admin_id,
            admin_name=args.admin_name,
            lease_seconds=args.lease_seconds,
            store=store,
        )
        if job:
            print(job["job_id"])
            return 0
        return 1

    if args.process_claimed:
        return 0 if _process_claimed(
            args.admin_id,
            args.admin_name,
            args.lease_seconds,
            args.verify_remote,
            store=store,
        ) >= 0 else 1

    if args.once:
        return 0 if run_once(
            args.admin_id,
            args.admin_name,
            args.lease_seconds,
            args.verify_remote,
            store=store,
        ) >= 0 else 1

    while True:
        run_once(
            args.admin_id,
            args.admin_name,
            args.lease_seconds,
            args.verify_remote,
            store=store,
        )
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
