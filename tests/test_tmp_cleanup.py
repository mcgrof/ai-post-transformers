"""Tests for /tmp artifact cleanup in the podcast pipeline.

Stale files in /tmp filled the disk on monster because podcast_*
directories from create_podcast were never removed. These tests
cover both the in-run cleanup (cleanup_podcast_tmpdir) and the
startup sweeper (sweep_stale_podcast_tmp) that handles artifacts
from crashed prior runs.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from elevenlabs_client import (
    cleanup_podcast_tmpdir,
    sweep_stale_podcast_tmp,
)


class TestCleanupPodcastTmpdir:
    def test_removes_directory(self, tmp_path):
        podcast_dir = tmp_path / "podcast_abc123"
        podcast_dir.mkdir()
        (podcast_dir / "seg_000.mp3").write_bytes(b"data")
        (podcast_dir / "seg_001.mp3").write_bytes(b"more")

        cleanup_podcast_tmpdir(str(podcast_dir))

        assert not podcast_dir.exists()

    def test_handles_missing_directory(self):
        # Must not raise when the dir is already gone.
        cleanup_podcast_tmpdir("/tmp/nonexistent_podcast_dir_xyz")

    def test_handles_none(self):
        # Must not raise when called with None (e.g., generation
        # failed before tmpdir was assigned).
        cleanup_podcast_tmpdir(None)

    def test_handles_empty_string(self):
        cleanup_podcast_tmpdir("")


def _age_backwards(path, hours):
    """Set mtime/atime on path to N hours ago."""
    old = time.time() - (hours * 3600)
    os.utime(path, (old, old))


class TestSweepStalePodcastTmp:
    def test_removes_old_podcast_dirs(self, tmp_path):
        stale = tmp_path / "podcast_stale"
        stale.mkdir()
        (stale / "seg.mp3").write_bytes(b"x" * 1024)
        _age_backwards(stale / "seg.mp3", hours=3)
        _age_backwards(stale, hours=3)

        fresh = tmp_path / "podcast_fresh"
        fresh.mkdir()
        (fresh / "seg.mp3").write_bytes(b"y")

        removed = sweep_stale_podcast_tmp(
            max_age_hours=2, tmp_dir=str(tmp_path))

        assert removed == 1
        assert not stale.exists(), "Stale podcast_* dir must be removed"
        assert fresh.exists(), "Fresh podcast_* dir must be preserved"

    def test_removes_stale_kokoro_files(self, tmp_path):
        stale_wav = tmp_path / "kokoro_out_abc.wav"
        stale_py = tmp_path / "kokoro_tts_abc.py"
        stale_wav.write_bytes(b"x" * 2048)
        stale_py.write_text("# old script")
        _age_backwards(stale_wav, hours=3)
        _age_backwards(stale_py, hours=3)

        fresh_wav = tmp_path / "kokoro_out_xyz.wav"
        fresh_wav.write_bytes(b"y")

        removed = sweep_stale_podcast_tmp(
            max_age_hours=2, tmp_dir=str(tmp_path))

        assert removed == 2
        assert not stale_wav.exists()
        assert not stale_py.exists()
        assert fresh_wav.exists()

    def test_no_files_returns_zero(self, tmp_path):
        removed = sweep_stale_podcast_tmp(
            max_age_hours=2, tmp_dir=str(tmp_path))
        assert removed == 0

    def test_does_not_touch_unrelated_paths(self, tmp_path):
        # Files that don't match our prefixes must be left alone,
        # even if old, even if named suspiciously.
        unrelated = tmp_path / "something_else.wav"
        unrelated.write_bytes(b"z" * 1024)
        _age_backwards(unrelated, hours=10)

        sweep_stale_podcast_tmp(
            max_age_hours=2, tmp_dir=str(tmp_path))

        assert unrelated.exists()
