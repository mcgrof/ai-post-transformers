"""Regression tests for prompt sanitization at the LLM boundary.

PDF text extraction can emit unpaired surrogate codepoints. Passing
them to subprocess stdin raised UnicodeEncodeError ("surrogates not
allowed") and killed the generation — seen as a failed submission for
arXiv 2604.25975.
"""

from llm_backend import _sanitize_prompt


def test_lone_surrogate_is_removed():
    dirty = "before \ud83d after"
    clean = _sanitize_prompt(dirty)
    # Must be UTF-8 encodable — this is exactly what crashed before.
    clean.encode("utf-8")
    assert "before" in clean and "after" in clean


def test_surrogate_run_from_pdf_extraction():
    dirty = "loss of 3\udcc0\udcaf% on benchmark"
    clean = _sanitize_prompt(dirty)
    clean.encode("utf-8")
    assert "loss of 3" in clean and "% on benchmark" in clean


def test_clean_text_is_unchanged():
    text = "Schrödinger's naïve résumé — 中文 and a real emoji: 🚀"
    assert _sanitize_prompt(text) == text


def test_idempotent():
    dirty = "x \ud800 y"
    once = _sanitize_prompt(dirty)
    assert _sanitize_prompt(once) == once


def test_pdf_extraction_sanitizes_at_source():
    # The May 2026 failure resurfaced one frame earlier once llm_call
    # was guarded: staging the dirty paper text to a file crashed the
    # same way. The canonical fix is sanitizing extraction output.
    from pdf_utils import sanitize_text

    dirty = "loss curve \ud83d\x00 anomaly"
    clean = sanitize_text(dirty)
    clean.encode("utf-8")
    assert "loss curve" in clean and "anomaly" in clean
    assert sanitize_text("Schrödinger 中文 🚀") == "Schrödinger 中文 🚀"
