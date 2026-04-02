"""Tests for the admin allowlist capability-gating module."""

from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest
import yaml

from scripts.admin_allowlist import (
    is_authorized,
    list_admins_with,
    load_admin_config,
    _build_lookup,
)


# ---- helpers ----

def _write_config(tmp_path: Path, admin_section: dict) -> Path:
    """Write a minimal config.yaml with the given admin section."""
    cfg = {"admin": admin_section}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg))
    return path


# ---- load_admin_config ----

def test_load_admin_config_returns_empty_on_missing_section(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("queue:\n  top_n: 30\n")
    result = load_admin_config(path)
    assert result == {}


def test_load_admin_config_returns_admin_section(tmp_path):
    admin_cfg = {"admins": [{"id": "alice", "capabilities": ["publish"]}]}
    path = _write_config(tmp_path, admin_cfg)
    result = load_admin_config(path)
    assert result == admin_cfg


# ---- _build_lookup ----

def test_build_lookup_empty():
    assert _build_lookup({}) == {}
    assert _build_lookup({"admins": []}) == {}


def test_build_lookup_skips_blank_ids():
    cfg = {"admins": [{"id": "", "capabilities": ["x"]}]}
    assert _build_lookup(cfg) == {}


def test_build_lookup_normalizes_capabilities():
    cfg = {"admins": [{"id": "bob", "capabilities": ["queue_refresh", " publish "]}]}
    lookup = _build_lookup(cfg)
    assert lookup == {"bob": {"queue_refresh", "publish"}}


# ---- is_authorized: default-deny ----

def test_default_deny_unknown_admin(tmp_path):
    path = _write_config(tmp_path, {
        "admins": [{"id": "mcgrof", "capabilities": ["queue_refresh"]}],
    })
    assert is_authorized("unknown", "queue_refresh", config_path=path) is False


def test_default_deny_no_admins_section(tmp_path):
    path = _write_config(tmp_path, {})
    assert is_authorized("mcgrof", "queue_refresh", config_path=path) is False


def test_default_deny_empty_admin_id(tmp_path):
    path = _write_config(tmp_path, {
        "admins": [{"id": "mcgrof", "capabilities": ["queue_refresh"]}],
    })
    assert is_authorized("", "queue_refresh", config_path=path) is False


def test_default_deny_empty_capability(tmp_path):
    path = _write_config(tmp_path, {
        "admins": [{"id": "mcgrof", "capabilities": ["queue_refresh"]}],
    })
    assert is_authorized("mcgrof", "", config_path=path) is False


def test_default_deny_wrong_capability(tmp_path):
    path = _write_config(tmp_path, {
        "admins": [{"id": "mcgrof", "capabilities": ["publish"]}],
    })
    assert is_authorized("mcgrof", "queue_refresh", config_path=path) is False


# ---- is_authorized: positive cases ----

def test_authorized_admin_with_capability(tmp_path):
    path = _write_config(tmp_path, {
        "admins": [{"id": "mcgrof", "capabilities": ["queue_refresh", "publish"]}],
    })
    assert is_authorized("mcgrof", "queue_refresh", config_path=path) is True
    assert is_authorized("mcgrof", "publish", config_path=path) is True


def test_authorized_via_admin_cfg_dict():
    """Passing admin_cfg directly avoids disk reads."""
    cfg = {"admins": [{"id": "alice", "capabilities": ["queue_refresh"]}]}
    assert is_authorized("alice", "queue_refresh", admin_cfg=cfg) is True
    assert is_authorized("alice", "publish", admin_cfg=cfg) is False


# ---- is_authorized: multiple admins ----

def test_multiple_admins_independent(tmp_path):
    path = _write_config(tmp_path, {
        "admins": [
            {"id": "mcgrof", "capabilities": ["queue_refresh", "publish"]},
            {"id": "alice", "capabilities": ["publish"]},
        ],
    })
    assert is_authorized("mcgrof", "queue_refresh", config_path=path) is True
    assert is_authorized("alice", "queue_refresh", config_path=path) is False
    assert is_authorized("alice", "publish", config_path=path) is True


# ---- list_admins_with ----

def test_list_admins_with_returns_matching():
    cfg = {
        "admins": [
            {"id": "mcgrof", "capabilities": ["queue_refresh", "publish"]},
            {"id": "alice", "capabilities": ["publish"]},
            {"id": "bob", "capabilities": ["queue_refresh"]},
        ],
    }
    result = list_admins_with("queue_refresh", admin_cfg=cfg)
    assert set(result) == {"mcgrof", "bob"}


def test_list_admins_with_returns_empty_for_unknown_cap():
    cfg = {"admins": [{"id": "mcgrof", "capabilities": ["publish"]}]}
    assert list_admins_with("queue_refresh", admin_cfg=cfg) == []


def test_list_admins_with_empty_config():
    assert list_admins_with("queue_refresh", admin_cfg={}) == []
