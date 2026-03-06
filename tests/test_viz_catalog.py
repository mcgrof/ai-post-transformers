"""Tests for visualization catalog sync.

All HTTP and DB calls are mocked. Tests cover caching logic,
URL resolution, episode matching, idempotent description updates,
and graceful HTTP error handling.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from viz_catalog import (
    _fetch_one,
    _build_viz_url,
    _match_visualizations,
    _update_episode_descriptions,
)


SAMPLE_CATALOG = {
    "base_url": "https://www.do-not-panic.com/visualizations/",
    "updated": "2026-03-05",
    "visualizations": [
        {
            "id": 72,
            "title": "PTP: Resolving the Independence Flaw",
            "url": "viz/2026/03/04/ptp-viz.html",
            "date": "2026-03-04",
            "papers": [
                {"id": "2404.19737", "type": "arxiv"},
                {"id": "2512.20856", "type": "arxiv"},
            ],
        }
    ],
}


class TestFetchOne:
    @patch("viz_catalog.requests.get")
    def test_catalog_changed_no_cache(self, mock_get, tmp_path):
        """First fetch with no cache should return the catalog."""
        content = json.dumps(SAMPLE_CATALOG).encode()
        mock_resp = MagicMock()
        mock_resp.content = content
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        name, catalog = _fetch_one("test", "https://example.com/c.json",
                                   tmp_path)
        assert catalog is not None
        assert len(catalog["visualizations"]) == 1
        assert (tmp_path / "test.json").exists()

    @patch("viz_catalog.requests.get")
    def test_catalog_changed_same_content(self, mock_get, tmp_path):
        """Identical content should return None (unchanged)."""
        content = json.dumps(SAMPLE_CATALOG).encode()
        (tmp_path / "test.json").write_bytes(content)

        mock_resp = MagicMock()
        mock_resp.content = content
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        name, catalog = _fetch_one("test", "https://example.com/c.json",
                                   tmp_path)
        assert catalog is None

    @patch("viz_catalog.requests.get")
    def test_catalog_changed_different(self, mock_get, tmp_path):
        """Changed content should return the new catalog."""
        old = json.dumps({"old": True}).encode()
        (tmp_path / "test.json").write_bytes(old)

        new_content = json.dumps(SAMPLE_CATALOG).encode()
        mock_resp = MagicMock()
        mock_resp.content = new_content
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        name, catalog = _fetch_one("test", "https://example.com/c.json",
                                   tmp_path)
        assert catalog is not None
        assert catalog["updated"] == "2026-03-05"

    @patch("viz_catalog.requests.get")
    def test_fetch_one_http_error(self, mock_get, tmp_path):
        """HTTP errors should log and return None."""
        import requests
        mock_get.side_effect = requests.RequestException("timeout")

        name, catalog = _fetch_one("test", "https://example.com/c.json",
                                   tmp_path)
        assert catalog is None


class TestBuildVizUrl:
    def test_relative_url(self):
        url = _build_viz_url(
            "https://www.do-not-panic.com/visualizations/",
            "viz/2026/03/04/ptp-viz.html",
        )
        assert url == "https://www.do-not-panic.com/viz/2026/03/04/ptp-viz.html"

    def test_absolute_url(self):
        abs_url = "https://other.com/viz/page.html"
        url = _build_viz_url("https://www.do-not-panic.com/", abs_url)
        assert url == abs_url

    def test_leading_slash_stripped(self):
        url = _build_viz_url(
            "https://example.com/base/",
            "/viz/page.html",
        )
        assert url == "https://example.com/viz/page.html"


class TestMatchVisualizations:
    def test_match_via_shared_arxiv_id(self):
        catalogs = [("test", SAMPLE_CATALOG)]
        arxiv_to_episodes = {
            "2404.19737": [{"id": 1, "title": "Episode on PTP"}],
        }
        matches = _match_visualizations(catalogs, arxiv_to_episodes)
        assert len(matches) == 1
        ep, title, url = matches[0]
        assert ep["id"] == 1
        assert "PTP" in title
        assert "ptp-viz.html" in url

    def test_no_overlap(self):
        catalogs = [("test", SAMPLE_CATALOG)]
        arxiv_to_episodes = {
            "9999.99999": [{"id": 1, "title": "Unrelated"}],
        }
        matches = _match_visualizations(catalogs, arxiv_to_episodes)
        assert len(matches) == 0

    def test_deduplicates_same_episode_viz(self):
        """When two arXiv IDs in the same viz map to the same episode,
        only one match should be produced."""
        catalogs = [("test", SAMPLE_CATALOG)]
        ep = {"id": 1, "title": "Episode on PTP"}
        arxiv_to_episodes = {
            "2404.19737": [ep],
            "2512.20856": [ep],
        }
        matches = _match_visualizations(catalogs, arxiv_to_episodes)
        assert len(matches) == 1


class TestUpdateDescriptions:
    def _make_conn(self):
        """Create an in-memory SQLite DB with podcasts table."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE podcasts (
                id INTEGER PRIMARY KEY,
                title TEXT,
                publish_date TEXT,
                spotify_url TEXT,
                elevenlabs_project_id TEXT,
                audio_file TEXT,
                source_urls TEXT,
                description TEXT,
                image_file TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            INSERT INTO podcasts (id, title, publish_date, description,
                                  created_at)
            VALUES (1, 'Test Episode', '2026-03-01', 'Original desc.',
                    '2026-03-01T00:00:00Z')
        """)
        conn.commit()
        return conn

    def test_appends_viz_link(self):
        conn = self._make_conn()
        matches = [(
            {"id": 1, "description": "Original desc."},
            "PTP Viz",
            "https://example.com/viz.html",
        )]
        updated = _update_episode_descriptions(matches, conn)
        assert updated == 1
        row = conn.execute(
            "SELECT description FROM podcasts WHERE id = 1"
        ).fetchone()
        assert "https://example.com/viz.html" in row[0]
        assert "PTP Viz" in row[0]
        conn.close()

    def test_idempotent_no_duplicate(self):
        conn = self._make_conn()
        desc_with_link = (
            "Original desc.\n\n"
            "Interactive Visualization: PTP Viz\n"
            "https://example.com/viz.html"
        )
        conn.execute(
            "UPDATE podcasts SET description = ? WHERE id = 1",
            (desc_with_link,)
        )
        conn.commit()

        matches = [(
            {"id": 1, "description": desc_with_link},
            "PTP Viz",
            "https://example.com/viz.html",
        )]
        updated = _update_episode_descriptions(matches, conn)
        assert updated == 0
        row = conn.execute(
            "SELECT description FROM podcasts WHERE id = 1"
        ).fetchone()
        assert row[0].count("https://example.com/viz.html") == 1
        conn.close()
