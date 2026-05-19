"""Tests for final editorial queue partitioning."""

from paper_queue import build_final_queue
from paper_record import PaperRecord


def _rec(arxiv_id, title, *, status="Cover now", public=0.0, memory=0.0,
         quality=0.5, badges=None):
    return PaperRecord(
        arxiv_id=arxiv_id,
        title=title,
        status=status,
        public_interest_score=public,
        memory_score=memory,
        bridge_score=min(public, memory),
        max_axis_score=max(public, memory),
        quality_score=quality,
        teachability=0.8,
        badges=list(badges or []),
    )


def test_memory_bucket_uses_relevance_not_axis_dominance():
    reviewed = [
        _rec("public-1", "Public 1", public=0.92, memory=0.10),
        _rec("memory-adj", "Memory Adjacent",
             public=0.62, memory=0.28,
             badges=["Memory/Storage Adjacent"]),
    ]
    sections = build_final_queue(reviewed, reviewed, {
        "editorial": {
            "final_queue": {"bridge": 0, "public": 1, "memory": 1}
        }
    })

    assert [rec.arxiv_id for rec in sections["public"]] == ["public-1"]
    assert [rec.arxiv_id for rec in sections["memory"]] == ["memory-adj"]
    assert "Memory-relevant" in sections["memory"][0].badges


def test_memory_backfill_uses_ranking_not_strict_predicate():
    reviewed = [
        _rec("public-1", "Public 1", public=0.95, memory=0.08),
        _rec("memory-1", "Memory 1", public=0.20, memory=0.44,
             badges=["Memory/Storage Core"]),
        _rec("monitor-1", "Monitor Memory",
             status="Monitor", public=0.48, memory=0.32, quality=0.9,
             badges=["Memory/Storage Adjacent"]),
    ]
    sections = build_final_queue(reviewed, reviewed, {
        "editorial": {
            "final_queue": {"bridge": 0, "public": 1, "memory": 2}
        }
    })

    assert [rec.arxiv_id for rec in sections["memory"]] == [
        "memory-1", "monitor-1"
    ]


def test_memory_bucket_labels_first_vs_relevant():
    reviewed = [
        _rec("memory-first", "Memory First", public=0.22, memory=0.61,
             badges=["Memory/Storage Core"]),
        _rec("memory-relevant", "Memory Relevant", public=0.57, memory=0.29,
             badges=["Memory/Storage Adjacent"]),
    ]
    sections = build_final_queue(reviewed, reviewed, {
        "editorial": {
            "final_queue": {"bridge": 0, "public": 0, "memory": 2}
        }
    })

    badge_map = {
        rec.arxiv_id: set(rec.badges) for rec in sections["memory"]
    }
    assert "Memory-first" in badge_map["memory-first"]
    assert "Memory-relevant" in badge_map["memory-relevant"]


# Regression tests for the queue.html embedding bug where re.subn
# halved literal backslashes in the JSON-encoded `const QUEUE = ...`
# block, turning JSON-escaped LaTeX like `"\\%"` into `"\%"`. Modern
# V8 throws SyntaxError on invalid string escapes (`\%`, `\textbf`),
# aborts the <script>, and the page renders as a blank shell.
# See paper_queue.py:generate_queue_html_v2 (~line 1500).

def test_queue_html_preserves_backslashes_in_latex_abstracts():
    """`re.subn` with a string replacement halves runs of literal
    backslashes (they're treated as backreferences). The production
    code must therefore pass the replacement via a callable so the
    JSON-escaped LaTeX in abstracts (`\\%`, `\\textbf{...}`) survives
    intact into the rendered queue.html.
    """
    import json
    import re

    # Realistic abstracts: arXiv summaries that include raw LaTeX
    # (`\%`, `\textbf{...}`, `\cite{...}` etc.) — json.dumps doubles
    # the backslashes to produce a valid JSON string literal.
    sections = {
        "public": [
            {
                "arxiv_id": "9999.99999",
                "title": "Latex Survival Test",
                "abstract": (
                    "We achieve 128x compression with less than "
                    "3\\% accuracy loss using \\textbf{block-wise} "
                    "decomposition."
                ),
            }
        ],
    }
    queue_json = json.dumps(sections, ensure_ascii=False)
    # Sanity: json.dumps must produce doubled backslashes in source
    assert '\\\\%' in queue_json
    assert '\\\\textbf' in queue_json

    template = "const QUEUE = OLD_DATA;\n\nconst CATEGORY_META = {};"
    replacement = (
        f"const QUEUE = {queue_json};\n\nconst CATEGORY_META ="
    )

    # The BROKEN form — re.subn with a string replacement — halves
    # the backslashes. We assert the bug here to make sure future
    # readers see why the production code must not be written this
    # way.
    buggy, _ = re.subn(
        r"const QUEUE = .*?;\n\nconst CATEGORY_META =",
        replacement,
        template,
        count=1,
        flags=re.S,
    )
    assert '\\\\%' not in buggy, (
        "string-replacement form must demonstrate the bug — re.subn "
        "should halve the doubled backslashes"
    )
    assert '\\%' in buggy
    # `\%` in a JS string literal is an invalid escape and V8 will
    # SyntaxError. That's the failure mode we are guarding against.

    # The CORRECT form — re.subn with a callable replacement —
    # preserves backslashes literally. This is what generate_
    # queue_html_v2 must use.
    fixed, _ = re.subn(
        r"const QUEUE = .*?;\n\nconst CATEGORY_META =",
        lambda _m: replacement,
        template,
        count=1,
        flags=re.S,
    )
    assert '\\\\%' in fixed, (
        "lambda-replacement form must preserve doubled backslashes "
        "intact through the template substitution"
    )
    assert '\\\\textbf' in fixed


def test_generate_queue_html_v2_emits_valid_js_string_escapes(tmp_path):
    """End-to-end: after running generate_queue_html_v2 against a
    sections dict that contains LaTeX-escaped backslashes, the
    rendered queue.html must contain those backslashes doubled
    (valid JSON string literal → valid JS string literal) so the
    `const QUEUE = ...` script block doesn't throw at parse time.
    """
    from pathlib import Path
    import paper_queue

    # Point the function at a tmp working tree so we don't clobber
    # the real podcasts/queue.html. The function uses
    # Path(__file__).parent — patch __file__ to a tmp path that has
    # a podcasts/ subdir with the template copied in.
    tmp_root = tmp_path / "repo"
    (tmp_root / "podcasts").mkdir(parents=True)
    real_template = (
        Path(paper_queue.__file__).parent / "podcasts" / "queue2.html"
    )
    (tmp_root / "podcasts" / "queue2.html").write_text(
        real_template.read_text()
    )
    fake_pq = tmp_root / "paper_queue.py"
    fake_pq.write_text("# stub\n")

    sections = {
        "public": [
            {
                "arxiv_id": "9999.99999",
                "title": "Latex Survival",
                "abstract": "3\\% loss with \\textbf{128x}",
                "authors": ["A. Test"],
                "published_at": "2026-05-19T00:00:00",
                "categories": ["cs.LG"],
                "url": "http://arxiv.org/abs/9999.99999",
            }
        ],
        "memory": [], "bridge": [], "monitor": [],
        "deferred": [], "out_of_scope": [],
    }

    # Monkey-patch the module's __file__ for the duration of the
    # call so the hard-coded Path(__file__).parent resolves into
    # our tmp_root.
    saved_file = paper_queue.__file__
    paper_queue.__file__ = str(fake_pq)
    try:
        paper_queue.generate_queue_html_v2(sections, config={})
    finally:
        paper_queue.__file__ = saved_file

    rendered = (tmp_root / "podcasts" / "queue.html").read_text()
    # The doubled-backslash JSON escapes must survive intact
    assert '\\\\%' in rendered, (
        "expected doubled backslash before % in rendered queue.html"
    )
    assert '\\\\textbf' in rendered, (
        "expected doubled backslash before textbf in rendered "
        "queue.html"
    )
    # And the single-backslash invalid-JS form must NOT appear in
    # the abstract string we provided (i.e. the bug must not have
    # silently re-occurred).
    # Find the abstract's surrounding context and verify only the
    # doubled form is present.
    assert '3\\%' not in rendered.replace('3\\\\%', 'OK')
