"""Tests for source metadata inference (no torch dependencies)."""

import pytest


def test_infers_transformer_circuits_pub_as_anthropic():
    """Papers from transformer-circuits.pub should be tagged as Anthropic."""
    # Import locally to avoid conftest torch loading
    from elevenlabs_client import _infer_source_metadata

    text = """
    This article is published on transformer-circuits.pub
    Verbalizable Representations Form a Global Workspace...
    """
    meta = _infer_source_metadata(text)
    assert meta.get("source_type") == "Anthropic Interpretability Research"
    assert meta.get("is_anthropic") is True
    assert meta.get("affiliation") == "Anthropic"


def test_infers_lesswrong_blog_posts():
    """Blog posts from LessWrong should be detected."""
    from elevenlabs_client import _infer_source_metadata

    text = """
    This is from lesswrong.com
    The logit lens and how to interpret it...
    """
    meta = _infer_source_metadata(text)
    assert meta.get("source_type") == "LessWrong blog post"
    assert meta.get("pub_venue") == "LessWrong"


def test_infers_arxiv_preprints():
    """Standard arXiv preprints should be detected."""
    from elevenlabs_client import _infer_source_metadata

    text = """
    This paper is available at arxiv.org/abs/2401.12345
    """
    meta = _infer_source_metadata(text)
    assert meta.get("source_type") == "Preprint server"
    assert meta.get("pub_venue") == "arXiv"


def test_empty_text_returns_no_metadata():
    """If no source markers are found, return empty dict."""
    from elevenlabs_client import _infer_source_metadata

    text = "This is just some random text with no source attribution."
    meta = _infer_source_metadata(text)
    assert meta == {}


def test_recognizes_transformer_circuits_html_source():
    """HTML pages from transformer-circuits.pub should be recognized."""
    from elevenlabs_client import _infer_source_metadata

    html_text = """
    Transformer Circuits

    Verbalizable Representations Form a Global Workspace in Language Models

    By the Anthropic Interpretability Team

    [rest of article]
    """
    meta = _infer_source_metadata(html_text)
    assert meta.get("is_anthropic") is True
