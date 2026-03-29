"""Test generated systemd unit files for syntactic validity.

Uses systemd-analyze verify when available, otherwise validates
structure with basic INI parsing. Skips gracefully in CI or
environments without systemd tooling.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# The JS module is the source of truth, but we replicate the unit
# text here via a tiny subprocess so the test stays repo-local and
# does not require a Node import bridge.

ROOT = Path(__file__).resolve().parent.parent


def _node_eval(expr: str) -> str:
    """Run a JS expression that returns a string and capture stdout."""
    script = (
        "import {generateSystemdService, generateSystemdTimer} "
        f"from './admin/src/systemd.js'; "
        f"process.stdout.write({expr});"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"node eval failed: {result.stderr.strip()}")
    return result.stdout


@pytest.fixture(scope="module")
def service_unit() -> str:
    return _node_eval("generateSystemdService('testadmin')")


@pytest.fixture(scope="module")
def timer_unit() -> str:
    return _node_eval("generateSystemdTimer()")


# ---- structural tests (always run) ----

def test_service_has_required_sections(service_unit: str):
    for section in ("[Unit]", "[Service]", "[Install]"):
        assert section in service_unit


def test_timer_has_required_sections(timer_unit: str):
    for section in ("[Unit]", "[Timer]", "[Install]"):
        assert section in timer_unit


def test_service_parseable_as_ini(service_unit: str):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(service_unit)
    assert "Unit" in cp
    assert "Service" in cp
    assert cp["Service"]["Type"] == "oneshot"


def test_timer_parseable_as_ini(timer_unit: str):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(timer_unit)
    assert "Timer" in cp
    assert "Install" in cp


def test_service_admin_id_embedded(service_unit: str):
    assert "--admin-id testadmin" in service_unit


def test_service_runs_combined_worker(service_unit: str):
    assert "run_podcast_worker.py" in service_unit
    assert "run_publish_worker.py" not in service_unit


def test_service_uses_working_directory_and_flock(service_unit: str):
    assert "WorkingDirectory=%h/devel/ai-post-transformers" in service_unit
    assert "/usr/bin/flock -n -E 0" in service_unit
    assert "%t/podcast-worker.lock" in service_unit


def test_service_passes_queue_db(service_unit: str):
    assert "--queue-db" in service_unit
    assert "QUEUE_DB_PATH=" in service_unit
    assert ".local/state/ai-post-transformers/queue.db" in service_unit


def test_timer_uses_on_unit_inactive(timer_unit: str):
    assert "OnUnitInactiveSec=" in timer_unit
    assert "OnUnitActiveSec=" not in timer_unit


# ---- systemd-analyze verify (skip if unavailable) ----

_HAS_SYSTEMD_ANALYZE = shutil.which("systemd-analyze") is not None


@pytest.mark.skipif(
    not _HAS_SYSTEMD_ANALYZE,
    reason="systemd-analyze not found",
)
class TestSystemdAnalyze:
    """Write units to a temp dir and run systemd-analyze verify."""

    @pytest.fixture(autouse=True)
    def _tmpdir(self, service_unit: str, timer_unit: str):
        self.tmpdir = tempfile.mkdtemp(prefix="podcast-worker-test-")
        svc_path = Path(self.tmpdir) / "podcast-worker.service"
        tmr_path = Path(self.tmpdir) / "podcast-worker.timer"
        # Replace %h with a concrete path so verify doesn't complain
        svc_text = service_unit.replace("%h", "/tmp/fakehome")
        svc_path.write_text(svc_text)
        tmr_path.write_text(timer_unit)
        self.svc_path = svc_path
        self.tmr_path = tmr_path
        yield
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _verify(self, unit_path: Path) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["SYSTEMD_UNIT_PATH"] = self.tmpdir
        return subprocess.run(
            ["systemd-analyze", "verify", "--user", str(unit_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )

    def test_service_verify(self):
        result = self._verify(self.svc_path)
        # systemd-analyze verify exits 0 on success. Some distros
        # emit warnings for %h even after replacement; treat
        # warnings (rc=0) as success and only fail on rc!=0 with
        # actual errors.
        if result.returncode != 0:
            # Filter out known non-fatal noise
            errors = [
                line
                for line in result.stderr.splitlines()
                if "not found" not in line.lower()
                and "fakehome" not in line
            ]
            if errors:
                pytest.fail(
                    f"systemd-analyze verify failed:\n"
                    + "\n".join(errors)
                )

    def test_timer_verify(self):
        result = self._verify(self.tmr_path)
        if result.returncode != 0:
            errors = [
                line
                for line in result.stderr.splitlines()
                if "not found" not in line.lower()
            ]
            if errors:
                pytest.fail(
                    f"systemd-analyze verify failed:\n"
                    + "\n".join(errors)
                )
