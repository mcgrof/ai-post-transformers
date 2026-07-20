"""Google Scholar search-URL construction and repair.

Episode descriptions carry a "Sources:" block whose fallback links are
Google Scholar title searches. These URLs were historically built with a
naive ``title.replace(" ", "+")`` that never percent-encoded colons,
ampersands, slashes, brackets, or non-ASCII characters. An unencoded
``&`` is the worst case: it terminates the query string, so Scholar only
receives the text before it (``Better & Faster ...`` searched just
``Better``). Colons, ``[MASK]``, ``(GPT-3)``, ``/`` and ``Gödel`` all
produced malformed or wrong searches too.

This module builds correct URLs (:func:`scholar_search_url`) and repairs
old ones in place (:func:`repair_description`) without ever altering the
title a URL searches for.

Repair strategy
---------------
The naive encoding only mapped space -> ``+`` and changed nothing else,
so ``len(q) == len(title)`` and every ``+`` in a stored ``q=`` value is
either an original space or a literal ``+``. The description line above
each URL (``  N. Title — authors, year``) is the authoritative title
text but can be truncated at an internal ``" — "``. We overlay the two
signals positionally and then enforce a hard invariant: a URL is
rewritten only when the reconstructed title re-encodes to exactly the
stored ``q=`` value. That guarantees the repair is faithful (never
corrupts a title) and idempotent (already-encoded URLs no longer satisfy
the naive invariant, so they are left untouched).
"""

import re
import urllib.parse

_SCHOLAR_BASE = "https://scholar.google.com/scholar?q="

# A Scholar URL sitting alone on a description line, with an optional
# trailing "&as_ylo=YYYY" recency filter and optional surrounding space.
_SCHOLAR_LINE_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<base>https://scholar\.google\.com/scholar\?q=)"
    r"(?P<q>\S+?)"
    r"(?P<ylo>&as_ylo=\d+)?"
    r"(?P<trail>\s*)$"
)
_NUM_PREFIX_RE = re.compile(r"^\s*\d+\.\s+(?P<body>.*)$")
_TITLE_SEP = " — "  # em-dash separator between title and authors/year

# An inline Scholar URL (mid-line), used for legacy feeds whose source
# blocks are flattened onto a single line ("... 2. Title — a, y URL 3. ").
# The q-value runs up to the next whitespace, with an optional &as_ylo.
_SCHOLAR_INLINE_RE = re.compile(
    r"(?P<base>https://scholar\.google\.com/scholar\?q=)"
    r"(?P<q>[^\s<\"]+?)"
    r"(?P<ylo>&as_ylo=\d+)?"
    r"(?=[\s<\"]|$)"
)


def scholar_search_url(title):
    """Return a correctly percent-encoded Scholar search URL for a title."""
    return _SCHOLAR_BASE + urllib.parse.quote_plus(title or "")


def _title_from_source_line(line):
    """Recover a source title from a ``  N. Title — authors, year`` line.

    Returns None when the line is not a numbered source entry.
    """
    m = _NUM_PREFIX_RE.match(line)
    if not m:
        return None
    return m.group("body").split(_TITLE_SEP, 1)[0].strip()


def _reconstruct_title(qval, line_title):
    """Recover the true title behind a naive ``q=`` value.

    ``line_title`` (from the paired description line) supplies the exact
    characters, including any literal ``+``; where it runs short a ``+``
    in ``qval`` is decoded as a space. The caller enforces the hard
    invariant, so an imperfect reconstruction is simply never applied.
    """
    decoded = qval.replace("+", " ")
    if not line_title:
        return decoded
    chars = list(decoded)
    for i in range(min(len(chars), len(line_title))):
        chars[i] = line_title[i]
    return "".join(chars)


def repair_description(text):
    """Re-encode every Scholar URL in a description.

    Returns ``(new_text, n_fixed)``. Safe (never changes the searched
    title), idempotent, and a no-op for already-correct URLs.
    """
    if not text or "scholar.google" not in text:
        return text, 0
    lines = text.split("\n")
    out = []
    prev_title = None
    n_fixed = 0
    for line in lines:
        m = _SCHOLAR_LINE_RE.match(line)
        if not m:
            out.append(line)
            title = _title_from_source_line(line)
            if title:
                prev_title = title
            continue
        qval = m.group("q")
        recon = _reconstruct_title(qval, prev_title)
        # Hard invariant: only rewrite when the reconstructed title
        # re-encodes to exactly the stored q-value. This never alters
        # the searched title and makes the pass idempotent (an already
        # percent-encoded q-value fails this check and is left as-is).
        if recon.replace(" ", "+") != qval:
            out.append(line)
            continue
        new_line = (
            m.group("indent")
            + scholar_search_url(recon)
            + (m.group("ylo") or "")
            + m.group("trail")
        )
        if new_line != line:
            n_fixed += 1
        out.append(new_line)
    return "\n".join(out), n_fixed


def _inline_title_before(text, pos):
    """Recover the source title for an inline URL ending at ``pos``.

    Looks back within a bounded window for the ``" — "`` that ends the
    title and the entry's ``N.`` number marker before it. Returns None
    when no clean marker is found, in which case the caller falls back
    to decoding the q-value directly.
    """
    window = text[max(0, pos - 500):pos]
    sep = window.rfind(_TITLE_SEP)
    if sep == -1:
        return None
    head = window[:sep]
    last = None
    for last in re.finditer(r"(?:^|[\s>])\d+\.\s", head):
        pass
    if last is None:
        return None
    return head[last.end():].strip()


def repair_inline_urls(text):
    """Repair Scholar URLs embedded mid-line (flattened source blocks).

    Legacy feeds keep the whole "Sources:" block on a single line, so
    the URL is not alone on its line and :func:`repair_description`
    cannot see it. The title is recovered from the nearest preceding
    ``N. Title — `` marker; the same hard invariant guards every
    rewrite, so a title is never altered and the pass is idempotent.
    """
    if not text or "scholar.google" not in text:
        return text, 0
    counter = {"n": 0}

    def _sub(m):
        qval = m.group("q")
        line_title = _inline_title_before(text, m.start())
        recon = _reconstruct_title(qval, line_title)
        if recon.replace(" ", "+") != qval:
            return m.group(0)
        new = (m.group("base") + urllib.parse.quote_plus(recon)
               + (m.group("ylo") or ""))
        if new != m.group(0):
            counter["n"] += 1
        return new

    return _SCHOLAR_INLINE_RE.sub(_sub, text), counter["n"]
