"""Local fallback episode cover renderer.

When the external image-generation API (currently OpenAI gpt-image-1)
is unavailable — billing cap reached, missing API key, content-policy
rejection, transient HTTP error — episodes used to get no cover at
all. That surfaced as a blank `.card-img-placeholder` div on the
front page in the best case, and as a broken-image icon in the worst.

This module renders a 1024x1024 PNG locally using PIL: dark
deterministic gradient + the show name across the top + the episode
title centered + the domain footer + a left accent stripe. The
accent and dark colors are derived from a SHA-256 of the title so
each episode looks visually distinct.

The renderer has zero external dependencies beyond Pillow (already
required) and falls back to PIL's default bitmap font if no
TrueType font is installed.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional, Tuple

# Try a few sensible TTF locations (Debian/Ubuntu first, then macOS
# Homebrew, then nothing — fall back to PIL bitmap default).
_FONT_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/opt/homebrew/share/fonts/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)
_FONT_REG_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/opt/homebrew/share/fonts/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _first_existing(candidates) -> Optional[str]:
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _color_from_title(
    title: str,
) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    """Deterministic (accent, dark) color from the episode title."""
    h = hashlib.sha256(title.encode("utf-8")).digest()
    accent = (h[0] // 2 + 60, h[1] // 2 + 60, h[2] // 2 + 80)
    dark = (h[3] // 4 + 8, h[4] // 4 + 8, h[5] // 4 + 12)
    return accent, dark


def _load_font(path: Optional[str], size: int):
    """Load a TTF; fall back to PIL bitmap default if path is None
    or the load fails for any reason."""
    from PIL import ImageFont
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # No TTF available — use the bitmap default. It's tiny but
    # produces a readable (if ugly) cover so the fallback still
    # works in containers without fonts installed.
    return ImageFont.load_default()


def _wrap_lines(draw, text: str, font, max_w: int):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if draw.textlength(candidate, font=font) <= max_w:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_title_cover(
    title: str,
    output_path,
    *,
    show_name: str = "AI POST TRANSFORMERS",
    domain: str = "podcast.do-not-panic.com",
    size: int = 1024,
) -> Optional[str]:
    """Render a 1024x1024 episode cover PNG locally.

    Used as a fallback when external image generation is
    unavailable. Returns ``str(output_path)`` on success or
    ``None`` if Pillow is not installed (any other rendering
    failure raises).

    The output is visually distinct per-title (color hash) but
    plain — it does NOT pretend to be AI-generated cover art. The
    intent is "this episode shipped without OpenAI succeeding;
    here is something coherent rather than a blank slot."
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    accent, dark = _color_from_title(title)
    bg = Image.new("RGB", (size, size), dark)
    px = bg.load()
    for y in range(size):
        t = y / (size - 1)
        r = int(dark[0] * (1 - t) + accent[0] * t * 0.5)
        g = int(dark[1] * (1 - t) + accent[1] * t * 0.5)
        b = int(dark[2] * (1 - t) + accent[2] * t * 0.5)
        for x in range(size):
            px[x, y] = (r, g, b)

    draw = ImageDraw.Draw(bg)

    # Left accent stripe so the card looks composed, not just a
    # flat gradient.
    stripe_w = max(12, size // 60)
    draw.rectangle([(0, 0), (stripe_w, size)], fill=accent)

    bold_path = _first_existing(_FONT_BOLD_CANDIDATES)
    reg_path = _first_existing(_FONT_REG_CANDIDATES)

    # Show name (top).
    show_font = _load_font(bold_path, max(24, size // 28))
    margin = max(32, size // 18)
    draw.text((margin, margin + 16), show_name,
              fill=(255, 255, 255), font=show_font)
    underline_y = margin + 16 + max(36, size // 28) + 6
    underline_w = min(size // 3, 360)
    draw.line(
        [(margin, underline_y), (margin + underline_w, underline_y)],
        fill=accent, width=max(2, size // 340),
    )

    # Episode title (centered vertically). Try sizes from large to
    # small until the wrapped text fits in ≤ 4 lines within the
    # canvas.
    title_max_w = size - 2 * margin
    candidate_sizes = (96, 88, 80, 72, 64, 56, 48, 40, 32)
    chosen_font = None
    chosen_lines = []
    chosen_line_h = 0
    for fs in candidate_sizes:
        font = _load_font(bold_path, fs)
        lines = _wrap_lines(draw, title, font, title_max_w)
        line_h = int(fs * 1.15)
        total_h = len(lines) * line_h
        if len(lines) <= 4 and total_h < size - 280:
            chosen_font = font
            chosen_lines = lines
            chosen_line_h = line_h
            break
    if chosen_font is None:
        # Title is huge — accept the smallest size and let it
        # overflow vertically rather than fail.
        chosen_font = _load_font(bold_path, candidate_sizes[-1])
        chosen_lines = _wrap_lines(
            draw, title, chosen_font, title_max_w,
        )
        chosen_line_h = int(candidate_sizes[-1] * 1.15)

    total_h = len(chosen_lines) * chosen_line_h
    y0 = (size - total_h) // 2
    for i, line in enumerate(chosen_lines):
        draw.text((margin, y0 + i * chosen_line_h),
                  line, fill=(245, 245, 245), font=chosen_font)

    # Footer.
    foot_font = _load_font(reg_path, max(20, size // 36))
    draw.text((margin, size - margin - 8), domain,
              fill=(210, 210, 210), font=foot_font)

    bg.save(str(output_path), format="PNG", optimize=True)
    return str(output_path)


def render_webp_thumb(
    png_path,
    webp_path,
    *,
    width: int = 400,
    quality: int = 82,
) -> Optional[str]:
    """Downsize a cover PNG to a WebP thumbnail at ``width`` px.

    Same shape contract as ``render_title_cover``: returns the
    path on success, None if Pillow is missing.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    png_path = Path(png_path)
    webp_path = Path(webp_path)
    webp_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(png_path).convert("RGB")
    ratio = width / img.width
    new_size = (width, int(img.height * ratio))
    img = img.resize(new_size, Image.Resampling.LANCZOS)
    img.save(str(webp_path), format="WEBP", quality=quality, method=6)
    return str(webp_path)
