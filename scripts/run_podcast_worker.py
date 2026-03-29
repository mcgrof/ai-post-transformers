#!/usr/bin/env python3
"""Combined podcast worker: generation pickup then publish pickup.

This is the single automation entry point used by the systemd timer.
Each invocation runs two phases in order:

  1. Generation phase — check for pending submissions in R2 (or SQLite
     when --queue-db is set) and generate a draft for the first one
     found.
  2. Publish phase — claim and process the next approved publish job.

When --queue-db is provided the worker operates in bridged mode:
  - Before work: import new/updated R2 records into SQLite.
  - Generation and publish both run against the SQLite QueueStore.
  - After work: export newer SQLite records back to R2.

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

from scripts.queue_bridge import _summary_string, sync_down, sync_up


def _run_generation_phase(admin_id: str, *, store=None) -> int:
    """Run the generation worker for one submission.

    Returns 1 if a submission was processed, 0 otherwise.
    Failures are caught so the publish phase still runs.
    """
    try:
        from scripts.run_generation_worker import run_once as gen_run_once
        return gen_run_once(admin_id, store=store)
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


def _bridge_import(store) -> None:
    """Import R2 state into SQLite before the worker cycle."""
    try:
        summary = sync_down(store)
        print(_summary_string("down", summary))
    except Exception as exc:
        print(f"[podcast-worker] Bridge import error: {exc}")


def _bridge_export(store) -> None:
    """Export SQLite state back to R2 after the worker cycle."""
    try:
        summary = sync_up(store)
        print(_summary_string("up", summary))
    except Exception as exc:
        print(f"[podcast-worker] Bridge export error: {exc}")


def run_once(
    admin_id: str,
    admin_name: str | None,
    lease_seconds: int,
    verify_remote: bool,
    *,
    store,
    queue_db_store=None,
) -> int:
    """Run one generation + publish cycle.

    When *queue_db_store* is set, the bridge sync wraps the cycle:
    import from R2 before work, export back after.  Both phases use
    the SQLite store for all reads and writes.
    """
    if queue_db_store is not None:
        _bridge_import(queue_db_store)

    gen_store = queue_db_store
    pub_store = queue_db_store if queue_db_store is not None else store

    gen_count = _run_generation_phase(admin_id, store=gen_store)
    pub_count = _run_publish_phase(
        admin_id, admin_name, lease_seconds, verify_remote, store=pub_store
    )

    if queue_db_store is not None:
        _bridge_export(queue_db_store)

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
        help="publish job record backend (used when --queue-db is not set)",
    )
    parser.add_argument("--local-root")
    parser.add_argument(
        "--queue-db",
        help="Path to SQLite queue database. Enables bridged mode: "
             "R2 import -> SQLite work -> R2 export each cycle.",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Run the R2 <-> SQLite bridge only, without processing work.",
    )
    args = parser.parse_args()

    if not any((args.once, args.loop, args.sync_only)):
        parser.error("choose --once, --loop, or --sync-only")
    if args.sync_only and not args.queue_db:
        parser.error("--sync-only requires --queue-db")

    queue_db_store = None
    if args.queue_db:
        queue_db_path = Path(args.queue_db).expanduser()
        queue_db_path.parent.mkdir(parents=True, exist_ok=True)
        from scripts.queue_store import get_queue_store
        queue_db_store = get_queue_store(mode="sqlite", path=queue_db_path)

    if args.sync_only:
        _bridge_import(queue_db_store)
        _bridge_export(queue_db_store)
        return 0

    from scripts.publish_job_store import get_publish_job_store

    store = None
    if queue_db_store is None:
        store = get_publish_job_store(
            mode=args.store,
            root=Path(args.local_root) if args.local_root else None,
        )

    def _cycle():
        return run_once(
            args.admin_id, args.admin_name, args.lease_seconds,
            args.verify_remote, store=store,
            queue_db_store=queue_db_store,
        )

    if args.once:
        _cycle()
        return 0

    while True:
        _cycle()
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
