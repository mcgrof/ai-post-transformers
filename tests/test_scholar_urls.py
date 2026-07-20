"""Regression tests for Scholar search-URL construction and repair.

Episode descriptions embedded Scholar links built by a naive
``title.replace(" ", "+")`` that never percent-encoded special
characters. An unencoded ``&`` truncated the query (``Better & Faster``
searched only ``Better``); colons, brackets, slashes, and non-ASCII all
produced wrong searches. These tests pin the correct encoding and the
faithful, idempotent repair.
"""

import urllib.parse

from scholar_urls import (
    scholar_search_url,
    repair_description,
    repair_inline_urls,
)


# --- construction -------------------------------------------------------
def test_plain_title_encodes_spaces():
    assert scholar_search_url("Attention Is All You Need") == (
        "https://scholar.google.com/scholar?q=Attention+Is+All+You+Need"
    )


def test_ampersand_is_percent_encoded_not_left_raw():
    url = scholar_search_url("Better & Faster Large Language Models")
    assert "%26" in url and "&" not in url.split("?q=", 1)[1]
    # And the whole title survives the round-trip through the query string.
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["q"][0] == "Better & Faster Large Language Models"


def test_colon_bracket_slash_unicode_encoded():
    url = scholar_search_url("Gödel: [MASK]/C++ Study")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["q"][0] == "Gödel: [MASK]/C++ Study"


def test_none_title_is_safe():
    assert scholar_search_url(None) == "https://scholar.google.com/scholar?q="


# --- repair: multi-line descriptions ------------------------------------
def _desc(*urls):
    lines = ["Some summary.", "", "Sources:"]
    for i, (title, url) in enumerate(urls, 1):
        lines.append(f"  {i}. {title} — Some Author, 2024")
        lines.append(f"     {url}")
    return "\n".join(lines)


def test_repair_fixes_ampersand_truncation():
    bad = "https://scholar.google.com/scholar?q=Better+&+Faster+LLMs+via+MTP"
    desc = _desc(("Better & Faster LLMs via MTP", bad))
    fixed, n = repair_description(desc)
    assert n == 1
    assert "q=Better+%26+Faster+LLMs+via+MTP" in fixed
    # what Scholar now receives is the full title, not just "Better"
    url = [l.strip() for l in fixed.split("\n") if "scholar" in l][0]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["q"][0] == "Better & Faster LLMs via MTP"


def test_repair_preserves_literal_plus_from_title_line():
    # REINFORCE++ must survive — naive reverse-decode would lose the ++.
    bad = ("https://scholar.google.com/scholar?q="
           "REINFORCE++:+Stabilizing+Critic-Free+Policy+Optimization")
    desc = _desc(("REINFORCE++: Stabilizing Critic-Free Policy Optimization", bad))
    fixed, n = repair_description(desc)
    assert n == 1
    url = [l.strip() for l in fixed.split("\n") if "scholar" in l][0]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["q"][0] == "REINFORCE++: Stabilizing Critic-Free Policy Optimization"


def test_repair_handles_internal_em_dash_subtitle():
    title = ("Mooncake: Trading More Storage for Less Computation — "
             "A KVCache-centric Architecture")
    bad = ("https://scholar.google.com/scholar?q="
           + title.replace(" ", "+"))
    desc = _desc((title, bad))
    fixed, n = repair_description(desc)
    assert n == 1
    url = [l.strip() for l in fixed.split("\n") if "scholar" in l][0]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    # full title including the subtitle after the internal em-dash
    assert q["q"][0] == title


def test_repair_leaves_already_correct_urls_untouched():
    good = "https://scholar.google.com/scholar?q=Attention+Is+All+You+Need"
    desc = _desc(("Attention Is All You Need", good))
    fixed, n = repair_description(desc)
    assert n == 0
    assert fixed == desc


def test_repair_is_idempotent():
    bad = ("https://scholar.google.com/scholar?q="
           "Medusa:+Simple+LLM+Inference+&+[MASK]+Decoding")
    desc = _desc(("Medusa: Simple LLM Inference & [MASK] Decoding", bad))
    once, n1 = repair_description(desc)
    twice, n2 = repair_description(once)
    assert n1 == 1
    assert n2 == 0
    assert twice == once


def test_repair_never_alters_the_searched_title_invariant():
    # Across a batch of adversarial titles the parsed query must always
    # equal the human-readable title on the line above.
    titles = [
        "EAGLE: Speculative Sampling Requires Rethinking Feature Uncertainty",
        "Language Models are Few-Shot Learners (GPT-3)",
        "Conditional [MASK] Discrete Diffusion Language Model",
        "Gödel Machines: Fully Self-Referential Optimal Universal Self-Improvers",
        "PagedAttention / vLLM: Efficient Memory Management",
        "Do NOT Think That Much for 2+3=? On Overthinking",
    ]
    pairs = [(t, "https://scholar.google.com/scholar?q=" + t.replace(" ", "+"))
             for t in titles]
    desc = _desc(*pairs)
    fixed, n = repair_description(desc)
    assert n == len(titles)
    urls = [l.strip() for l in fixed.split("\n") if "scholar" in l]
    for title, url in zip(titles, urls):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["q"][0] == title
        # exactly one query parameter — no stray '&'-split params
        assert list(urllib.parse.parse_qs(
            urllib.parse.urlparse(url).query).keys()) == ["q"]


def test_repair_noop_when_no_scholar_urls():
    desc = "Just a summary.\n\nSources:\n  1. A paper — Author, 2024\n     https://arxiv.org/abs/1234"
    fixed, n = repair_description(desc)
    assert n == 0 and fixed == desc


# --- repair: inline (flattened legacy feed) -----------------------------
def test_inline_repair_flattened_source_block():
    text = ("Sources: 1. First Paper — A, 2020 "
            "https://scholar.google.com/scholar?q=First+Paper "
            "2. Medusa: Simple Inference — B, 2024 "
            "https://scholar.google.com/scholar?q=Medusa:+Simple+Inference "
            "3. Next — C, 2025")
    fixed, n = repair_inline_urls(text)
    assert n == 1  # only Medusa (has a colon); "First Paper" already fine
    assert "q=Medusa%3A+Simple+Inference" in fixed
    # surrounding text is untouched
    assert "Sources: 1. First Paper" in fixed and "3. Next — C, 2025" in fixed


def test_inline_repair_is_idempotent():
    text = ("2. A&B: A Study — X, 2021 "
            "https://scholar.google.com/scholar?q=A&B:+A+Study end")
    once, n1 = repair_inline_urls(text)
    twice, n2 = repair_inline_urls(once)
    assert n2 == 0 and twice == once
