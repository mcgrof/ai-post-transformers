"""Admin allowlist: capability-gated authorization for automation lanes.

The allowlist is loaded from the ``admin`` section of config.yaml:

    admin:
      admins:
        - id: mcgrof
          capabilities: [queue_refresh, publish]

Authorization is default-deny: an admin ID not listed, or listed without
the required capability, is rejected.  Adding a new admin or granting a
new capability is a config-only change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def load_admin_config(config_path: Path | str | None = None) -> dict:
    """Load and return the ``admin`` section from config.yaml."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg.get("admin", {})


def _build_lookup(admin_cfg: dict) -> dict[str, set[str]]:
    """Return {admin_id: {capability, ...}} from the config dict."""
    lookup: dict[str, set[str]] = {}
    for entry in admin_cfg.get("admins", []):
        aid = entry.get("id", "").strip()
        if not aid:
            continue
        caps = entry.get("capabilities", [])
        lookup[aid] = {str(c).strip() for c in caps if str(c).strip()}
    return lookup


def is_authorized(
    admin_id: str,
    capability: str,
    *,
    admin_cfg: dict | None = None,
    config_path: Path | str | None = None,
) -> bool:
    """Check whether *admin_id* holds *capability*.

    Pass *admin_cfg* (the ``admin:`` dict) directly when it is already
    loaded, or *config_path* to read from disk.  If neither is given the
    default ``config.yaml`` next to the repo root is used.

    Returns ``False`` for unknown admins, missing capabilities, or
    malformed config (default-deny).
    """
    if not admin_id or not capability:
        return False
    if admin_cfg is None:
        admin_cfg = load_admin_config(config_path)
    lookup = _build_lookup(admin_cfg)
    return capability in lookup.get(admin_id, set())


def list_admins_with(
    capability: str,
    *,
    admin_cfg: dict | None = None,
    config_path: Path | str | None = None,
) -> list[str]:
    """Return admin IDs that hold *capability*."""
    if admin_cfg is None:
        admin_cfg = load_admin_config(config_path)
    lookup = _build_lookup(admin_cfg)
    return [aid for aid, caps in lookup.items() if capability in caps]
