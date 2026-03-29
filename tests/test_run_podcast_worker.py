from __future__ import annotations

import sys

import pytest

from scripts import run_podcast_worker


def test_run_once_wraps_queue_db_cycle_with_bridge(monkeypatch):
    calls = []
    queue_store = object()

    monkeypatch.setattr(
        run_podcast_worker,
        "_bridge_import",
        lambda store: calls.append(("down", store)),
    )
    monkeypatch.setattr(
        run_podcast_worker,
        "_run_generation_phase",
        lambda admin_id, *, store=None: calls.append(("gen", store)) or 1,
    )
    monkeypatch.setattr(
        run_podcast_worker,
        "_run_publish_phase",
        lambda admin_id, admin_name, lease_seconds, verify_remote, *, store: calls.append(("pub", store)) or 1,
    )
    monkeypatch.setattr(
        run_podcast_worker,
        "_bridge_export",
        lambda store: calls.append(("up", store)),
    )

    total = run_podcast_worker.run_once(
        "mcgrof",
        "mcgrof",
        900,
        True,
        store=None,
        queue_db_store=queue_store,
    )

    assert total == 2
    assert calls == [
        ("down", queue_store),
        ("gen", queue_store),
        ("pub", queue_store),
        ("up", queue_store),
    ]


def test_main_sync_only_requires_queue_db(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_podcast_worker.py", "--admin-id", "mcgrof", "--sync-only"],
    )

    with pytest.raises(SystemExit) as exc:
        run_podcast_worker.main()

    assert exc.value.code == 2


def test_main_sync_only_runs_bridge(monkeypatch, tmp_path):
    events = []
    queue_db = tmp_path / "queue.db"

    monkeypatch.setattr(
        run_podcast_worker,
        "_bridge_import",
        lambda store: events.append(("down", store.describe())),
    )
    monkeypatch.setattr(
        run_podcast_worker,
        "_bridge_export",
        lambda store: events.append(("up", store.describe())),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_podcast_worker.py",
            "--admin-id",
            "mcgrof",
            "--sync-only",
            "--queue-db",
            str(queue_db),
        ],
    )

    assert run_podcast_worker.main() == 0
    assert events[0][0] == "down"
    assert events[1][0] == "up"
    assert events[0][1].startswith("sqlite:")
