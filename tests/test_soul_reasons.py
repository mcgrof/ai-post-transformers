"""Regression tests for opening-reason rotation.

The selector got permanently stuck on the first pool entry once every
reason had been used at least once (Ada opened ~50 episodes in a row
with "The theoretical grounding is what sold me on this one"). Rotation
must follow least-recently-used order instead.
"""
import sqlite3
import soul_reasons


def _conn_factory(rows):
    """Return a callable that yields a fresh seeded conn each call
    (select_opening_reason closes the conn it gets)."""
    def factory():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE podcasts (id INTEGER PRIMARY KEY, "
                     "primary_host TEXT, opening_reason TEXT)")
        for i, (host, reason) in enumerate(rows, 1):
            conn.execute("INSERT INTO podcasts (id, primary_host, "
                         "opening_reason) VALUES (?,?,?)", (i, host, reason))
        conn.commit()
        return conn
    return factory


def _setup(monkeypatch, pool, rows):
    monkeypatch.setattr(soul_reasons, "load_reason_pools",
                        lambda: {"Ada": {"reasons": pool}})
    monkeypatch.setattr(soul_reasons, "get_connection", _conn_factory(rows))
    monkeypatch.setattr(soul_reasons, "init_db", lambda c: None)


def test_never_used_reason_wins_first(monkeypatch):
    pool = ["reason-A", "reason-B", "reason-C"]
    # A used recently+repeatedly, B once (old), C never.
    _setup(monkeypatch, pool,
           [("Ada", "reason-B"), ("Ada", "reason-A"), ("Ada", "reason-A")])
    assert soul_reasons.select_opening_reason("Ada") == "reason-C"


def test_picks_oldest_when_all_used_not_the_first(monkeypatch):
    pool = ["reason-A", "reason-B", "reason-C"]
    # all used; B oldest (id 1), A newest (id 3).
    _setup(monkeypatch, pool,
           [("Ada", "reason-B"), ("Ada", "reason-C"), ("Ada", "reason-A")])
    assert soul_reasons.select_opening_reason("Ada") == "reason-B"
    assert soul_reasons.select_opening_reason("Ada") != "reason-A"


def test_the_actual_stuck_bug(monkeypatch):
    # The real shape: pool[0] used far more recently than all others.
    pool = ["theoretical grounding", "mathematical framework",
            "empirical validation"]
    rows = [("Ada", "mathematical framework"),
            ("Ada", "empirical validation")]
    rows += [("Ada", "theoretical grounding")] * 40  # dominates, newest
    _setup(monkeypatch, pool, rows)
    picked = soul_reasons.select_opening_reason("Ada")
    assert picked == "mathematical framework"  # oldest, not the stuck one


def test_unknown_character_returns_none(monkeypatch):
    monkeypatch.setattr(soul_reasons, "load_reason_pools", lambda: {})
    assert soul_reasons.select_opening_reason("Nobody") is None
