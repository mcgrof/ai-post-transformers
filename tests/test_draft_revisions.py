"""Tests for draft revision tracking and logical episode model."""

import sqlite3
import pytest

from db import get_connection, init_db, insert_podcast, update_podcast
from draft_revisions import (
    normalize_episode_key,
    detect_revision,
    assign_revision,
    find_logical_episode,
    approve_revision,
    reject_episode_drafts,
    mark_published,
    find_stale_published_drafts,
    cleanup_stale_published_drafts,
    backfill_episode_keys,
    get_active_drafts,
    get_revision_history,
)


@pytest.fixture
def conn(tmp_path):
    """In-memory SQLite connection with schema initialized."""
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    init_db(c)
    return c


def _insert(conn, title, audio="drafts/2026/03/test.mp3", **kw):
    """Helper to insert a podcast and return its id."""
    return insert_podcast(
        conn,
        title=title,
        publish_date=kw.get("publish_date", "2026-03-26"),
        audio_file=audio,
        **{k: v for k, v in kw.items() if k != "publish_date"},
    )


# ── normalize_episode_key tests ────────────────────────────────

class TestNormalizeEpisodeKey:
    def test_strips_episode_prefix(self):
        assert normalize_episode_key("Episode: Foo Bar") == "foo bar"

    def test_case_insensitive_prefix(self):
        assert normalize_episode_key("EPISODE: Foo Bar") == "foo bar"

    def test_removes_punctuation(self):
        key = normalize_episode_key("Hello, World! (2026)")
        assert key == "hello world 2026"

    def test_collapses_whitespace(self):
        key = normalize_episode_key("  Multiple   Spaces  Here  ")
        assert key == "multiple spaces here"

    def test_unicode_normalization(self):
        key = normalize_episode_key("Résumé of Naïve Approach")
        assert "resume" in key
        assert "naive" in key

    def test_empty_and_none(self):
        assert normalize_episode_key("") == ""
        assert normalize_episode_key(None) == ""

    def test_same_topic_different_forms(self):
        k1 = normalize_episode_key(
            "Episode: LeWorldModel: Stable Joint-Embedding World Models"
        )
        k2 = normalize_episode_key(
            "LeWorldModel: Stable Joint-Embedding World Models"
        )
        assert k1 == k2


# ── detect_revision tests ─────────────────────────────────────

class TestDetectRevision:
    def test_new_episode_becomes_v1(self, conn):
        key, rev, superseded = detect_revision(conn, "Brand New Topic")
        assert rev == 1
        assert superseded == []
        assert key == normalize_episode_key("Brand New Topic")

    def test_second_draft_becomes_v2(self, conn):
        pid = _insert(conn, "Episode: Foo Paper")
        key, rev1, _ = detect_revision(conn, "Episode: Foo Paper",
                                       exclude_id=pid)
        assign_revision(conn, pid, key, rev1)

        # Second draft for same topic
        pid2 = _insert(conn, "Foo Paper")
        key2, rev2, superseded = detect_revision(
            conn, "Foo Paper", exclude_id=pid2
        )
        assert key2 == key
        assert rev2 == 2
        assert pid in superseded

    def test_third_draft_becomes_v3(self, conn):
        pid1 = _insert(conn, "Topic A")
        key, r, _ = detect_revision(conn, "Topic A", exclude_id=pid1)
        assign_revision(conn, pid1, key, r)

        pid2 = _insert(conn, "Episode: Topic A")
        key, r, sup = detect_revision(conn, "Topic A",
                                      exclude_id=pid2)
        assign_revision(conn, pid2, key, r, sup)
        assert r == 2

        pid3 = _insert(conn, "Topic A")
        key, r, sup = detect_revision(conn, "Topic A",
                                      exclude_id=pid3)
        assert r == 3
        # Only the active revision (pid2) should be in superseded
        # since pid1 was already superseded
        assert pid2 in sup
        assert pid1 not in sup


# ── assign_revision tests ─────────────────────────────────────

class TestAssignRevision:
    def test_sets_episode_key_and_revision(self, conn):
        pid = _insert(conn, "Test Episode")
        assign_revision(conn, pid, "test episode", 1)

        row = conn.execute(
            "SELECT episode_key, revision, revision_state "
            "FROM podcasts WHERE id = ?", (pid,)
        ).fetchone()
        assert row["episode_key"] == "test episode"
        assert row["revision"] == 1
        assert row["revision_state"] == "active"

    def test_supersedes_older_revisions(self, conn):
        pid1 = _insert(conn, "Topic X")
        assign_revision(conn, pid1, "topic x", 1)

        pid2 = _insert(conn, "Topic X")
        assign_revision(conn, pid2, "topic x", 2, [pid1])

        r1 = conn.execute(
            "SELECT revision_state FROM podcasts WHERE id = ?", (pid1,)
        ).fetchone()
        r2 = conn.execute(
            "SELECT revision_state FROM podcasts WHERE id = ?", (pid2,)
        ).fetchone()
        assert r1["revision_state"] == "superseded"
        assert r2["revision_state"] == "active"


# ── approve_revision tests ────────────────────────────────────

class TestApproveRevision:
    def test_approve_promotes_latest_and_supersedes_older(self, conn):
        pid1 = _insert(conn, "Paper Z")
        assign_revision(conn, pid1, "paper z", 1)
        pid2 = _insert(conn, "Paper Z")
        assign_revision(conn, pid2, "paper z", 2, [pid1])

        # pid1 is already superseded. Now approve pid2.
        ep_key = approve_revision(conn, pid2)
        assert ep_key == "paper z"

        r2 = conn.execute(
            "SELECT revision_state FROM podcasts WHERE id = ?", (pid2,)
        ).fetchone()
        assert r2["revision_state"] == "approved"

    def test_approve_supersedes_remaining_active_revisions(self, conn):
        pid1 = _insert(conn, "Multi Rev")
        assign_revision(conn, pid1, "multi rev", 1)
        pid2 = _insert(conn, "Multi Rev")
        assign_revision(conn, pid2, "multi rev", 2)
        # Both are "active" (assign_revision only supersedes
        # explicitly passed IDs)

        # Force pid1 back to active for this test
        conn.execute(
            "UPDATE podcasts SET revision_state = 'active' WHERE id = ?",
            (pid1,),
        )
        conn.commit()

        approve_revision(conn, pid2)

        r1 = conn.execute(
            "SELECT revision_state FROM podcasts WHERE id = ?", (pid1,)
        ).fetchone()
        assert r1["revision_state"] == "superseded"


# ── reject_episode_drafts tests ───────────────────────────────

class TestRejectEpisodeDrafts:
    def test_rejects_all_active_drafts(self, conn):
        pid1 = _insert(conn, "Bad Topic")
        assign_revision(conn, pid1, "bad topic", 1)
        pid2 = _insert(conn, "Bad Topic")
        assign_revision(conn, pid2, "bad topic", 2, [pid1])

        rejected = reject_episode_drafts(conn, "bad topic")
        assert set(rejected) == {pid1, pid2}

        for pid in [pid1, pid2]:
            row = conn.execute(
                "SELECT revision_state FROM podcasts WHERE id = ?",
                (pid,),
            ).fetchone()
            assert row["revision_state"] == "rejected"

    def test_does_not_reject_published_episodes(self, conn):
        pid1 = _insert(conn, "Published Topic")
        assign_revision(conn, pid1, "published topic", 1)
        # Simulate already published
        conn.execute(
            "UPDATE podcasts SET published_at = '2026-03-20T00:00:00Z' "
            "WHERE id = ?", (pid1,)
        )
        conn.commit()

        pid2 = _insert(conn, "Published Topic")
        assign_revision(conn, pid2, "published topic", 2)

        rejected = reject_episode_drafts(conn, "published topic")
        # Only pid2 (unpublished) should be rejected
        assert rejected == [pid2]

        r1 = conn.execute(
            "SELECT revision_state FROM podcasts WHERE id = ?", (pid1,)
        ).fetchone()
        assert r1["revision_state"] != "rejected"

    def test_reject_returns_empty_for_unknown_key(self, conn):
        assert reject_episode_drafts(conn, "nonexistent") == []
        assert reject_episode_drafts(conn, "") == []


# ── mark_published tests ──────────────────────────────────────

class TestMarkPublished:
    def test_marks_published_and_supersedes_others(self, conn):
        pid1 = _insert(conn, "Pub Test")
        assign_revision(conn, pid1, "pub test", 1)
        pid2 = _insert(conn, "Pub Test")
        assign_revision(conn, pid2, "pub test", 2, [pid1])
        # Approve pid2 first
        approve_revision(conn, pid2)

        ep_key = mark_published(conn, pid2)
        assert ep_key == "pub test"

        r2 = conn.execute(
            "SELECT revision_state FROM podcasts WHERE id = ?", (pid2,)
        ).fetchone()
        assert r2["revision_state"] == "published"

    def test_returns_none_for_missing_id(self, conn):
        assert mark_published(conn, 9999) is None


# ── stale draft cleanup tests (LeWorldModel-style) ────────────

class TestStaleDraftCleanup:
    def test_finds_stale_published_drafts(self, conn):
        pid = _insert(conn, "Episode: LeWorldModel")
        # Simulate: already published but revision_state still active
        conn.execute(
            "UPDATE podcasts SET published_at = '2026-03-01T00:00:00Z' "
            "WHERE id = ?", (pid,)
        )
        conn.commit()

        stale = find_stale_published_drafts(conn)
        assert len(stale) == 1
        assert stale[0]["id"] == pid

    def test_cleanup_marks_stale_as_published(self, conn):
        pid = _insert(conn, "Episode: LeWorldModel")
        conn.execute(
            "UPDATE podcasts SET published_at = '2026-03-01T00:00:00Z' "
            "WHERE id = ?", (pid,)
        )
        conn.commit()

        count = cleanup_stale_published_drafts(conn)
        assert count == 1

        row = conn.execute(
            "SELECT revision_state, episode_key FROM podcasts WHERE id = ?",
            (pid,)
        ).fetchone()
        assert row["revision_state"] == "published"
        assert row["episode_key"] == normalize_episode_key("Episode: LeWorldModel")

    def test_no_stale_when_revision_state_already_set(self, conn):
        pid = _insert(conn, "Already Clean")
        conn.execute(
            "UPDATE podcasts SET published_at = '2026-03-01T00:00:00Z', "
            "revision_state = 'published' WHERE id = ?", (pid,)
        )
        conn.commit()

        stale = find_stale_published_drafts(conn)
        assert len(stale) == 0


# ── backfill_episode_keys tests ───────────────────────────────

class TestBackfillEpisodeKeys:
    def test_backfills_missing_keys(self, conn):
        pid1 = _insert(conn, "Topic Alpha")
        pid2 = _insert(conn, "Topic Beta")

        count = backfill_episode_keys(conn)
        assert count == 2

        for pid, expected in [(pid1, "topic alpha"), (pid2, "topic beta")]:
            row = conn.execute(
                "SELECT episode_key FROM podcasts WHERE id = ?", (pid,)
            ).fetchone()
            assert row["episode_key"] == expected

    def test_does_not_overwrite_existing_keys(self, conn):
        pid = _insert(conn, "Custom Key")
        conn.execute(
            "UPDATE podcasts SET episode_key = 'manual-key' WHERE id = ?",
            (pid,),
        )
        conn.commit()

        count = backfill_episode_keys(conn)
        assert count == 0


# ── get_active_drafts tests ───────────────────────────────────

class TestGetActiveDrafts:
    def test_returns_only_active_unpublished(self, conn):
        pid1 = _insert(conn, "Active Draft")
        assign_revision(conn, pid1, "active draft", 1)

        pid2 = _insert(conn, "Published One")
        assign_revision(conn, pid2, "published one", 1)
        conn.execute(
            "UPDATE podcasts SET published_at = '2026-03-01T00:00:00Z', "
            "revision_state = 'published' WHERE id = ?", (pid2,)
        )
        conn.commit()

        pid3 = _insert(conn, "Rejected One")
        assign_revision(conn, pid3, "rejected one", 1)
        conn.execute(
            "UPDATE podcasts SET revision_state = 'rejected' WHERE id = ?",
            (pid3,),
        )
        conn.commit()

        active = get_active_drafts(conn)
        active_ids = [d["id"] for d in active]
        assert pid1 in active_ids
        assert pid2 not in active_ids
        assert pid3 not in active_ids

    def test_deduplicates_by_episode_key(self, conn):
        pid1 = _insert(conn, "Same Topic")
        assign_revision(conn, pid1, "same topic", 1)
        pid2 = _insert(conn, "Same Topic")
        assign_revision(conn, pid2, "same topic", 2)
        # Both are active (not superseded in this test)
        conn.execute(
            "UPDATE podcasts SET revision_state = 'active' WHERE id IN (?, ?)",
            (pid1, pid2),
        )
        conn.commit()

        active = get_active_drafts(conn)
        keys = [d.get("episode_key") for d in active]
        assert keys.count("same topic") == 1


# ── get_revision_history tests ────────────────────────────────

class TestGetRevisionHistory:
    def test_returns_ordered_revisions(self, conn):
        pid1 = _insert(conn, "History Test")
        assign_revision(conn, pid1, "history test", 1)
        pid2 = _insert(conn, "History Test")
        assign_revision(conn, pid2, "history test", 2, [pid1])
        pid3 = _insert(conn, "History Test")
        assign_revision(conn, pid3, "history test", 3, [pid2])

        history = get_revision_history(conn, "history test")
        assert len(history) == 3
        assert history[0]["revision"] == 1
        assert history[1]["revision"] == 2
        assert history[2]["revision"] == 3

    def test_empty_for_unknown_key(self, conn):
        assert get_revision_history(conn, "nonexistent") == []
        assert get_revision_history(conn, "") == []
