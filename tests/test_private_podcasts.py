"""Tests for private podcast isolation invariants."""

import json
import sqlite3
from pathlib import Path

from db import (
    get_connection,
    get_private_podcasts,
    init_db,
    insert_podcast,
    list_podcasts,
)
from owner_token import owner_token


def _make_test_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Patch get_connection to use temp DB
    init_db(conn)
    return conn


def test_rss_excludes_private_episodes(tmp_path):
    """Private episodes must not appear in the public RSS feed."""
    conn = _make_test_db(tmp_path)

    insert_podcast(conn, "Public Episode", "2026-03-30",
                   audio_file="public/2026/03/public-ep.mp3",
                   visibility="public")
    insert_podcast(conn, "Private Episode", "2026-03-30",
                   audio_file="public/2026/03/private-ep.mp3",
                   visibility="private", owner="admin@test.com")

    episodes = list_podcasts(conn)
    # Default listing excludes private
    titles = [ep["title"] for ep in episodes]
    assert "Public Episode" in titles
    assert "Private Episode" not in titles

    # Simulate the RSS filter logic from rss.py
    all_eps = list_podcasts(conn, include_private=True)
    published = [
        ep for ep in all_eps
        if "/drafts/" not in (ep.get("audio_file") or "")
        and ep.get("visibility", "public") != "private"
    ]
    pub_titles = [ep["title"] for ep in published]
    assert "Public Episode" in pub_titles
    assert "Private Episode" not in pub_titles
    conn.close()


def test_get_private_podcasts_returns_only_owner(tmp_path):
    """get_private_podcasts must return only the specified owner's episodes."""
    conn = _make_test_db(tmp_path)

    insert_podcast(conn, "Alice Private", "2026-03-30",
                   visibility="private", owner="alice@test.com")
    insert_podcast(conn, "Bob Private", "2026-03-30",
                   visibility="private", owner="bob@test.com")
    insert_podcast(conn, "Public Episode", "2026-03-30",
                   visibility="public")

    alice_eps = get_private_podcasts(conn, "alice@test.com")
    assert len(alice_eps) == 1
    assert alice_eps[0]["title"] == "Alice Private"

    bob_eps = get_private_podcasts(conn, "bob@test.com")
    assert len(bob_eps) == 1
    assert bob_eps[0]["title"] == "Bob Private"

    nobody_eps = get_private_podcasts(conn, "nobody@test.com")
    assert len(nobody_eps) == 0
    conn.close()


def test_private_episodes_excluded_from_generation_context(tmp_path):
    """Private episodes must be filtered from prior-episode catalog."""
    conn = _make_test_db(tmp_path)

    insert_podcast(conn, "Public Ep", "2026-03-30",
                   audio_file="public/2026/03/pub.mp3",
                   visibility="public")
    insert_podcast(conn, "Private Ep", "2026-03-30",
                   audio_file="public/2026/03/priv.mp3",
                   visibility="private", owner="admin@test.com")

    # Simulate the filter from elevenlabs_client.py
    all_eps = list_podcasts(conn, include_private=True)
    catalog = []
    for ep in all_eps:
        if ep.get("visibility", "public") == "private":
            continue
        catalog.append(ep)

    titles = [ep["title"] for ep in catalog]
    assert "Public Ep" in titles
    assert "Private Ep" not in titles
    conn.close()


def test_list_podcasts_include_private_flag(tmp_path):
    """list_podcasts with include_private=True returns all episodes."""
    conn = _make_test_db(tmp_path)

    insert_podcast(conn, "Public", "2026-03-30", visibility="public")
    insert_podcast(conn, "Private", "2026-03-30",
                   visibility="private", owner="owner@test.com")

    public_only = list_podcasts(conn)
    assert len(public_only) == 1
    assert public_only[0]["title"] == "Public"

    all_eps = list_podcasts(conn, include_private=True)
    assert len(all_eps) == 2
    conn.close()


def test_owner_token_deterministic():
    """owner_token must return the same value for the same email."""
    t1 = owner_token("test@example.com")
    t2 = owner_token("test@example.com")
    assert t1 == t2
    assert len(t1) == 16
    assert "@" not in t1
    assert "test" not in t1


def test_owner_token_case_insensitive():
    """owner_token must be case-insensitive."""
    t1 = owner_token("Test@Example.COM")
    t2 = owner_token("test@example.com")
    assert t1 == t2


def test_owner_token_matches_js():
    """owner_token must match the JS ownerTokenSync output.

    Verified with: node -e "require('crypto').createHash('sha256')
      .update('owner-1@test.com').digest('hex').slice(0,16)"
    """
    assert owner_token("owner-1@test.com") == "bb026207927ee37e"


def test_private_storage_prefix_uses_opaque_token():
    """Private episode R2 keys must use opaque token, not raw email."""
    email = "admin@example.com"
    token = owner_token(email)
    prefix = f"private-episodes/{token}/"
    assert "@" not in prefix
    assert "admin" not in prefix
    assert email not in prefix
