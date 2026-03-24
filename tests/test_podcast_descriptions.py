import sqlite3

from podcast import _extract_arxiv_id, _format_source_entry, _resolve_source_info
from db import init_db, upsert_paper


def test_extract_arxiv_id_from_common_urls():
    assert _extract_arxiv_id("https://arxiv.org/pdf/2505.20334") == "2505.20334"
    assert _extract_arxiv_id("https://arxiv.org/pdf/2505.20334v1.pdf") == "2505.20334v1"
    assert _extract_arxiv_id("https://arxiv.org/abs/2405.21060") == "2405.21060"
    assert _extract_arxiv_id("https://example.com/paper.pdf") is None


def test_resolve_source_info_prefers_db_metadata_for_arxiv():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    upsert_paper(
        conn,
        {
            "arxiv_id": "2505.20334",
            "title": "Lookahead Q-Cache: Achieving More Consistent KV Cache Eviction via Pseudo Query",
            "authors": ["A. Author", "B. Author"],
            "published": "2025-05-28T00:00:00Z",
            "arxiv_url": "http://arxiv.org/abs/2505.20334",
            "abstract": "",
            "categories": ["cs.LG"],
            "source": "test",
        },
    )

    info = _resolve_source_info(conn, "https://arxiv.org/pdf/2505.20334", fallback_title="Fallback")
    assert info["title"].startswith("Lookahead Q-Cache")
    assert info["authors"] == "A. Author, B. Author"
    assert info["year"] == "2025"
    assert info["url"] == "http://arxiv.org/abs/2505.20334"


def test_format_source_entry_uses_title_not_bare_url():
    lines = _format_source_entry(
        1,
        "FlashAttention-4: Fast and Accurate Attention",
        "Tri Dao, Jay Shah",
        "2026",
        "https://arxiv.org/pdf/2603.05451v1",
    )
    assert lines[0] == "  1. FlashAttention-4: Fast and Accurate Attention — Tri Dao, Jay Shah, 2026"
    assert lines[1] == "     https://arxiv.org/pdf/2603.05451v1"
