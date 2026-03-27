#!/usr/bin/env python3
"""Combined podcast worker: generation pickup then publish pickup.

This is the single automation entry point used by the systemd timer.
Each invocation runs two phases in order:

  1. Generation phase — check for pending submissions in R2 and
     generate a draft for the first one found.
  2. Publish phase — claim and process the next approved publish job.

Both phases are optional: if there is nothing to do in a phase it
silently moves on. The worker exits after one cycle (--once) or loops
with a configurable interval (--loop).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_generation_phase(admin_id: str) -> int:
    """Run the generation worker for one submission.

    Returns 1 if a submission was processed, 0 otherwise.
    Failures are caught so the publish phase still runs.
    """
    try:
        from scripts.run_generation_worker import run_once as gen_run_once
        return gen_run_once(admin_id)
    except Exception as exc:
        print(f"[podcast-worker] Generation phase error: {exc}")
        return 0


def _run_publish_phase(
    admin_id: str,
    admin_name: str | None,
    lease_seconds: int,
    verify_remote: bool,
    *,
    store,
) -> int:
    """Run the publish worker for one job.

    Returns count of jobs processed.
    """
    try:
        from scripts.run_publish_worker import run_once as pub_run_once
        return pub_run_once(
            admin_id, admin_name, lease_seconds, verify_remote, store=store
        )
    except Exception as exc:
        print(f"[podcast-worker] Publish phase error: {exc}")
        return 0


def run_once(
    admin_id: str,
    admin_name: str | None,
    lease_seconds: int,
    verify_remote: bool,
    *,
    store,
) -> int:
    """Run one generation + publish cycle."""
    gen_count = _run_generation_phase(admin_id)
    pub_count = _run_publish_phase(
        admin_id, admin_name, lease_seconds, verify_remote, store=store
    )
    total = gen_count + pub_count
    if total == 0:
        print("[podcast-worker] Nothing to do")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Combined podcast worker: generation + publish."
    )
    parser.add_argument("--admin-id", required=True)
    parser.add_argument("--admin-name")
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle then exit")
    parser.add_argument("--loop", action="store_true",
                        help="Loop indefinitely")
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument("--verify-remote", action="store_true")
    parser.add_argument(
        "--store",
        choices=("auto", "local", "r2"),
        default="auto",
        help="publish job record backend",
    )
    parser.add_argument("--local-root")
    args = parser.parse_args()

    if not any((args.once, args.loop)):
        parser.error("choose --once or --loop")

    from scripts.publish_job_store import get_publish_job_store

    store = get_publish_job_store(
        mode=args.store,
        root=Path(args.local_root) if args.local_root else None,
    )

    if args.once:
        run_once(
            args.admin_id, args.admin_name, args.lease_seconds,
            args.verify_remote, store=store,
        )
        return 0

    while True:
        run_once(
            args.admin_id, args.admin_name, args.lease_seconds,
            args.verify_remote, store=store,
        )
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
