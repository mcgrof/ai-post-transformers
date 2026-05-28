"""Tests for the PIL fallback episode cover renderer.

The fallback runs whenever external image generation (OpenAI) is
unavailable — billing cap, missing API key, content-policy
rejection, network error. It must:

  - actually write a PNG file on disk and return its path
  - produce a deterministic-per-title color palette (so the same
    title always renders the same cover, useful for caching /
    diffing / regression debugging)
  - handle very long titles by shrinking the font until it fits
  - handle empty / odd input strings without crashing
  - downsize cleanly to a WebP thumbnail
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_cover import (
    _color_from_title,
    render_title_cover,
    render_webp_thumb,
)


def test_render_title_cover_writes_png_and_returns_path(tmp_path):
    out = tmp_path / "ep.png"
    result = render_title_cover("Test Episode Title", out)
    assert result == str(out)
    assert out.exists()
    # PNG magic bytes
    with out.open("rb") as fh:
        assert fh.read(4) == b"\x89PNG"
    # Reasonable size — at least a few KB (not an empty file)
    assert out.stat().st_size > 4096


def test_render_title_cover_is_1024x1024_by_default(tmp_path):
    from PIL import Image
    out = tmp_path / "ep.png"
    render_title_cover("Hello", out)
    with Image.open(out) as img:
        assert img.size == (1024, 1024)


def test_render_title_cover_honors_explicit_size(tmp_path):
    from PIL import Image
    out = tmp_path / "ep.png"
    render_title_cover("Hello", out, size=512)
    with Image.open(out) as img:
        assert img.size == (512, 512)


def test_color_from_title_is_deterministic_per_title():
    """Same title always produces the same palette. Important so the
    fallback cover is stable across re-renders / different machines
    — useful for diffing if we ever want to verify a cover hasn't
    changed without comparing raw bytes."""
    a1 = _color_from_title("DFX: Multi-FPGA")
    a2 = _color_from_title("DFX: Multi-FPGA")
    assert a1 == a2
    b1 = _color_from_title("Trajectory Summaries")
    assert a1 != b1, "different titles should produce different palettes"


def test_render_title_cover_handles_very_long_title(tmp_path):
    out = tmp_path / "ep.png"
    long_title = (
        "When Many-Shot Chain of Thought Becomes Test Time Learning "
        "in Long Context Models with Asymmetric Memory and Cascade "
        "Distillation: A Survey of Recent Approaches and Empirical "
        "Findings Across Multiple Hardware Generations"
    )
    result = render_title_cover(long_title, out)
    assert result == str(out)
    assert out.exists()
    # Must not OOM or crash — produced file is still a valid PNG
    with out.open("rb") as fh:
        assert fh.read(4) == b"\x89PNG"


def test_render_title_cover_handles_empty_title(tmp_path):
    out = tmp_path / "ep.png"
    result = render_title_cover("", out)
    assert result == str(out)
    assert out.exists()


def test_render_title_cover_handles_unicode_and_punctuation(tmp_path):
    out = tmp_path / "ep.png"
    result = render_title_cover(
        "Mixture of Experts & RoPE: Why It Works (Sort Of)", out)
    assert result == str(out)
    assert out.exists()


def test_render_title_cover_creates_parent_dir(tmp_path):
    """Output path inside a not-yet-existing subdir must work."""
    out = tmp_path / "deep" / "nested" / "ep.png"
    result = render_title_cover("Nested Dir Test", out)
    assert result == str(out)
    assert out.exists()


def test_render_webp_thumb_downsizes_correctly(tmp_path):
    from PIL import Image
    src = tmp_path / "full.png"
    render_title_cover("Source", src)
    thumb = tmp_path / "thumb.webp"
    result = render_webp_thumb(src, thumb, width=400)
    assert result == str(thumb)
    assert thumb.exists()
    with Image.open(thumb) as img:
        assert img.width == 400
        assert img.height == 400  # 1024x1024 input → square output
    # WebP files are well under the source PNG size
    assert thumb.stat().st_size < src.stat().st_size


def test_render_webp_thumb_creates_parent_dir(tmp_path):
    src = tmp_path / "full.png"
    render_title_cover("Source", src)
    thumb = tmp_path / "thumbs" / "out.webp"
    render_webp_thumb(src, thumb, width=200)
    assert thumb.exists()
