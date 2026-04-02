"""Tests for the queue-refresh worker authorization and orchestration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_queue_worker import (
    run_once, _check_auth, _upload_queue_json, _upload_public_artifacts,
)


# ---- authorization gating ----

def test_unauthorized_admin_returns_zero():
    """An admin without queue_refresh capability must be rejected."""
    cfg = {"admins": [{"id": "mcgrof", "capabilities": ["publish"]}]}
    with patch("scripts.run_queue_worker._check_auth", return_value=False):
        assert run_once("stranger") == 0


def test_authorized_admin_proceeds(tmp_path):
    """An authorized admin triggers the queue refresh pipeline."""
    with patch("scripts.run_queue_worker._check_auth", return_value=True), \
         patch("scripts.run_queue_worker._run_queue_refresh", return_value=True) as mock_refresh, \
         patch("scripts.run_queue_worker._upload_queue_json", return_value=None), \
         patch("scripts.run_queue_worker._upload_public_artifacts", return_value=[]):
        result = run_once("mcgrof")
        assert result == 1
        mock_refresh.assert_called_once()


def test_check_auth_uses_allowlist():
    """_check_auth delegates to admin_allowlist.is_authorized."""
    cfg = {"admins": [{"id": "alice", "capabilities": ["queue_refresh"]}]}
    with patch("scripts.admin_allowlist.is_authorized", return_value=True) as mock:
        assert _check_auth("alice") is True
        mock.assert_called_once_with("alice", "queue_refresh")


# ---- queue refresh failure handling ----

def test_refresh_failure_returns_zero():
    """If the queue pipeline raises, the worker returns 0."""
    with patch("scripts.run_queue_worker._check_auth", return_value=True), \
         patch("scripts.run_queue_worker._run_queue_refresh",
               side_effect=RuntimeError("boom")):
        assert run_once("mcgrof") == 0


# ---- R2 upload ----

def test_upload_skips_when_no_queue_json(tmp_path, monkeypatch):
    """Upload is skipped when queue.json does not exist."""
    monkeypatch.setattr("scripts.run_queue_worker.QUEUE_JSON_PATH",
                        tmp_path / "nonexistent.json")
    assert _upload_queue_json() is None


def test_upload_skips_when_no_r2_creds(tmp_path, monkeypatch):
    """Upload is skipped when R2 env vars are missing."""
    qpath = tmp_path / "queue.json"
    qpath.write_text("{}")
    monkeypatch.setattr("scripts.run_queue_worker.QUEUE_JSON_PATH", qpath)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert _upload_queue_json() is None


def test_upload_calls_r2(tmp_path, monkeypatch):
    """When queue.json exists and creds are set, upload is attempted."""
    qpath = tmp_path / "queue.json"
    qpath.write_text('{"sections": {}}')
    monkeypatch.setattr("scripts.run_queue_worker.QUEUE_JSON_PATH", qpath)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://fake.r2.dev")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    mock_client = MagicMock()
    with patch("r2_upload.get_r2_client", return_value=mock_client) as gc, \
         patch("r2_upload.upload_file", return_value="queue/latest.json") as uf:
        result = _upload_queue_json()
        assert result == "queue/latest.json"
        uf.assert_called_once_with(
            mock_client,
            str(qpath),
            "queue/latest.json",
            content_type="application/json",
            bucket="podcast-admin",
        )


# ---- upload failure does not crash worker ----

def test_upload_failure_still_returns_success():
    """R2 upload failure is non-fatal — the worker still returns 1."""
    with patch("scripts.run_queue_worker._check_auth", return_value=True), \
         patch("scripts.run_queue_worker._run_queue_refresh", return_value=True), \
         patch("scripts.run_queue_worker._upload_queue_json",
               side_effect=RuntimeError("r2 down")), \
         patch("scripts.run_queue_worker._upload_public_artifacts",
               return_value=[]):
        assert run_once("mcgrof") == 1


# ---- public artifact upload ----

def test_public_upload_skips_when_no_creds(monkeypatch):
    """Public artifact upload is skipped when R2 env vars are missing."""
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert _upload_public_artifacts() == []


def test_public_upload_skips_missing_files(tmp_path, monkeypatch):
    """Missing local files are skipped individually."""
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://fake.r2.dev")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    # Point artifacts to nonexistent paths
    monkeypatch.setattr(
        "scripts.run_queue_worker.PUBLIC_QUEUE_ARTIFACTS",
        [(tmp_path / "missing.html", "queue.html", "text/html")],
    )
    mock_client = MagicMock()
    with patch("r2_upload.get_r2_client", return_value=mock_client), \
         patch("r2_upload.upload_file") as uf:
        result = _upload_public_artifacts()
        assert result == []
        uf.assert_not_called()


def test_public_upload_uploads_existing_files(tmp_path, monkeypatch):
    """Existing queue.html and queue.xml are uploaded to the public bucket."""
    html_path = tmp_path / "queue.html"
    xml_path = tmp_path / "queue.xml"
    html_path.write_text("<html>queue</html>")
    xml_path.write_text("<rss/>")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://fake.r2.dev")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(
        "scripts.run_queue_worker.PUBLIC_QUEUE_ARTIFACTS",
        [
            (html_path, "queue.html", "text/html"),
            (xml_path, "queue.xml", "application/xml"),
        ],
    )
    mock_client = MagicMock()
    with patch("r2_upload.get_r2_client", return_value=mock_client), \
         patch("r2_upload.upload_file") as uf:
        result = _upload_public_artifacts()
        assert result == ["queue.html", "queue.xml"]
        assert uf.call_count == 2
        # Verify correct bucket for public artifacts
        for call in uf.call_args_list:
            assert call.kwargs.get("bucket") or call[1] == "ai-post-transformers"
            assert call.kwargs["bucket"] == "ai-post-transformers"


def test_public_upload_partial_when_one_missing(tmp_path, monkeypatch):
    """If only one artifact exists, only that one is uploaded."""
    html_path = tmp_path / "queue.html"
    html_path.write_text("<html/>")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://fake.r2.dev")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(
        "scripts.run_queue_worker.PUBLIC_QUEUE_ARTIFACTS",
        [
            (html_path, "queue.html", "text/html"),
            (tmp_path / "missing.xml", "queue.xml", "application/xml"),
        ],
    )
    mock_client = MagicMock()
    with patch("r2_upload.get_r2_client", return_value=mock_client), \
         patch("r2_upload.upload_file") as uf:
        result = _upload_public_artifacts()
        assert result == ["queue.html"]
        uf.assert_called_once()


def test_public_upload_failure_nonfatal():
    """Public artifact upload failure does not crash the worker."""
    with patch("scripts.run_queue_worker._check_auth", return_value=True), \
         patch("scripts.run_queue_worker._run_queue_refresh", return_value=True), \
         patch("scripts.run_queue_worker._upload_queue_json", return_value=None), \
         patch("scripts.run_queue_worker._upload_public_artifacts",
               side_effect=RuntimeError("public upload boom")):
        assert run_once("mcgrof") == 1
