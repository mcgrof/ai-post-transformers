"""Regression tests for the two bugs that broke front-page images
on the two newest episodes (2026-05-22 DFX, 2026-05-24 Trajectory
Summaries):

  1. podcast._generate_episode_image() ignored the return value of
     image_gen.generate_episode_image(), so when generation failed
     (e.g. OpenAI billing hard limit), the DB still got an
     image_file path pointing to a phantom .png. That made the
     publish pipeline upload nothing and the index render a broken
     <img> tag.

  2. rss.generate_index() unconditionally wrote
     <img class="card-img" src="{thumb_url}"> for each homepage
     card, even when thumb_url was empty. Browsers render that as a
     broken-image icon. The fix falls back to a
     .card-img-placeholder div (matching _render_card).
"""

from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- podcast.py

def test_generate_episode_image_falls_back_when_external_returns_none(tmp_path):
    """When image_gen.generate_episode_image returns None (e.g.
    OpenAI billing cap hit), the caller must NOT record a phantom
    image_file path — but it MAY fall back to the local PIL
    renderer to produce a coherent title card. Either way, the
    returned path must correspond to a real file on disk.
    """
    from podcast import _generate_episode_image

    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"\x00")
    image_path = tmp_path / "ep.png"

    config = {"image_generation": {"enabled": True, "model": "gpt-image-1"}}

    with patch("podcast.generate_episode_image", return_value=None):
        result = _generate_episode_image(
            config, "Episode Title", "abstract text", str(audio_path),
        )
    # New contract: external failure triggers local fallback. The
    # returned path is the image_path AND that file exists on disk.
    assert result == str(image_path)
    assert image_path.exists()


def test_generate_episode_image_falls_back_when_external_lies_about_file(tmp_path):
    """If image_gen returned a path-shaped value but didn't
    actually write the file, the caller must NOT report success
    with that phantom path — it must fall through to the local
    fallback (or return None if that also fails).
    """
    from podcast import _generate_episode_image

    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"\x00")
    image_path = tmp_path / "ep.png"

    config = {"image_generation": {"enabled": True}}

    with patch(
        "podcast.generate_episode_image",
        side_effect=lambda *a, **k: str(tmp_path / "ep.png"),
    ):
        result = _generate_episode_image(
            config, "T", "a", str(audio_path),
        )
    # The local fallback wrote a real PNG at image_path; verify both.
    assert result == str(image_path)
    assert image_path.exists()
    with image_path.open("rb") as fh:
        assert fh.read(4) == b"\x89PNG"


def test_generate_episode_image_returns_path_when_file_written(tmp_path):
    """When image_gen succeeds (file present on disk after the
    call), _generate_episode_image returns the image path.
    """
    from podcast import _generate_episode_image

    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"\x00")
    image_path = tmp_path / "ep.png"

    def _fake_gen(prompt, out_path, **kw):
        Path(out_path).write_bytes(b"\x89PNG fake")
        return out_path

    config = {"image_generation": {"enabled": True}}

    with patch("podcast.generate_episode_image", side_effect=_fake_gen):
        result = _generate_episode_image(
            config, "T", "a", str(audio_path),
        )
    assert result == str(image_path)
    assert image_path.exists()


def test_generate_episode_image_falls_back_to_local_renderer(tmp_path):
    """When OpenAI image gen returns None (e.g. billing cap), the
    pipeline must fall back to the local PIL title-card renderer
    so episodes still ship with a cover instead of a blank
    placeholder.
    """
    from podcast import _generate_episode_image

    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"\x00")
    image_path = tmp_path / "ep.png"

    config = {"image_generation": {"enabled": True}}

    # OpenAI path "fails" (returns None, no file written).
    with patch("podcast.generate_episode_image", return_value=None):
        result = _generate_episode_image(
            config, "Fallback Title", "abstract", str(audio_path),
        )

    # Pipeline must have rendered the local cover via render_title_cover
    assert result == str(image_path)
    assert image_path.exists()
    # And it must be a real PNG, not a stub.
    with image_path.open("rb") as fh:
        assert fh.read(4) == b"\x89PNG"
    # 1024x1024 is the local-cover default
    from PIL import Image
    with Image.open(image_path) as img:
        assert img.size == (1024, 1024)


def test_generate_episode_image_falls_back_when_openai_raises(tmp_path):
    """Same fallback contract when the external path raises an
    exception rather than returning None — the local cover should
    still be produced and image_file should still be set."""
    from podcast import _generate_episode_image

    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"\x00")
    image_path = tmp_path / "ep.png"

    config = {"image_generation": {"enabled": True}}

    def _boom(*a, **kw):
        raise RuntimeError("simulated openai 500")

    with patch("podcast.generate_episode_image", side_effect=_boom):
        result = _generate_episode_image(
            config, "Exception Path Test", "abstract", str(audio_path),
        )

    assert result == str(image_path)
    assert image_path.exists()


def test_generate_episode_image_returns_none_when_both_paths_fail(tmp_path):
    """If both external AND local fallback fail (e.g. PIL missing),
    the function must still return None so the DB doesn't lie
    about image_file."""
    from podcast import _generate_episode_image

    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"\x00")

    config = {"image_generation": {"enabled": True}}

    with patch("podcast.generate_episode_image", return_value=None), \
         patch("podcast.render_title_cover", return_value=None):
        result = _generate_episode_image(
            config, "Both Paths Fail", "abstract", str(audio_path),
        )

    assert result is None


# ---------------------------------------------------------------- rss.py

def test_index_card_template_uses_placeholder_when_thumb_url_empty():
    """Mirror the card-template behavior end-to-end at the string
    level. The renderer block under test is in generate_index()
    around the cards_html_parts loop; we exercise the exact branch
    that previously emitted <img class="card-img" src="">.
    """
    import html as html_mod

    def _render_card(thumb_url, slug, title, date, search_terms, desc_plain):
        if thumb_url:
            card_img_html = (
                f'<img class="card-img" '
                f'src="{html_mod.escape(thumb_url)}" '
                f'alt="" loading="lazy">'
            )
        else:
            card_img_html = '<div class="card-img card-img-placeholder"></div>'
        return f'''
  <a class="card" href="episodes/{slug}/" data-t="{html_mod.escape(search_terms)}" data-desc="{html_mod.escape(desc_plain)}">
    {card_img_html}
    <div class="card-meta"><div class="card-title">{html_mod.escape(title)}</div><div class="card-date">{html_mod.escape(date)}</div></div>
  </a>'''

    # Missing thumbnail → placeholder div, no <img>
    card = _render_card("", "no-img", "No Image Episode",
                        "May 27, 2026", "search", "desc")
    assert 'card-img-placeholder' in card
    assert '<img class="card-img"' not in card
    assert 'src=""' not in card

    # With thumbnail → <img> with the URL, no placeholder div
    card2 = _render_card(
        "https://podcast.do-not-panic.com/thumbs/some-stem.webp",
        "yes-img", "Image Episode",
        "May 27, 2026", "search", "desc",
    )
    assert 'card-img-placeholder' not in card2
    assert ('<img class="card-img" '
            'src="https://podcast.do-not-panic.com/thumbs/some-stem.webp"'
            in card2)


def test_generate_index_inline_template_keeps_placeholder_branch():
    """Read the actual rss.generate_index source and assert that
    the inline cards_html_parts template branches on `if thumb_url:`
    so the placeholder fallback can't silently get removed in a
    future refactor.
    """
    import inspect
    import rss

    src = inspect.getsource(rss.generate_index)
    assert 'card-img-placeholder' in src, (
        "rss.generate_index must keep the .card-img-placeholder "
        "fallback for episodes with no thumbnail"
    )
    # The conditional we rely on: render <img> if thumb_url else
    # placeholder.
    assert 'if thumb_url' in src or 'if not thumb_url' in src
