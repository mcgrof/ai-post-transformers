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
