"""Tests for the first-pass editorial scorer.

Uses regression cases from tests/regression_cases.yaml to verify
taxonomy classification, relative score ordering, and editorial
filter behavior.
"""

import pytest
from paper_record import PaperRecord


class TestPaperRecord:
    def test_from_paper_dict_basic(self):
        d = {
            "arxiv_id": "2401.12345",
            "title": "Test Paper",
            "abstract": "A test abstract.",
            "authors": ["Alice", "Bob"],
            "categories": ["cs.LG"],
            "published": "2026-01-01T00:00:00Z",
            "arxiv_url": "http://arxiv.org/abs/2401.12345",
            "hf_daily": True,
            "source": "github-issue",
            "issue_number": 42,
        }
        rec = PaperRecord.from_paper_dict(d)
        assert rec.arxiv_id == "2401.12345"
        assert rec.title == "Test Paper"
        assert rec.hf_trending_flag is True
        assert rec.github_submission_flag is True
        assert rec.issue_number == 42

    def test_to_dict_excludes_embedding(self):
        rec = PaperRecord(arxiv_id="test", title="Test")
        rec.embedding = [1.0, 2.0, 3.0]
        d = rec.to_dict()
        assert "embedding" not in d
        assert d["arxiv_id"] == "test"

    def test_score_breakdown_format(self):
        rec = PaperRecord(
            arxiv_id="test", title="Test Paper",
            public_interest_score=0.5, memory_score=0.3,
            badges=["Public AI"], status="Cover now",
            why_now="Important research")
        breakdown = rec.score_breakdown()
        assert "Test Paper" in breakdown
        assert "public_interest: 0.500" in breakdown
        assert "badges: Public AI" in breakdown
        assert "status: Cover now" in breakdown


class TestTaxonomyClassification:
    """Test that regression cases get correct taxonomy buckets."""

    def test_optimizer_theory_scope(self, editorial_scorer,
                                    regression_cases):
        case = _find_case(regression_cases,
                          "generic_optimizer_theory")
        rec = _score_case(editorial_scorer, case)
        assert rec.scope_bucket == "foundation"
        assert rec.paper_type == "theory"

    def test_image_restoration_domain(self, editorial_scorer,
                                      regression_cases):
        case = _find_case(regression_cases,
                          "narrow_image_restoration")
        rec = _score_case(editorial_scorer, case)
        assert rec.domain_bucket == "vision"
        assert rec.narrow_domain_flag is True

    def test_llm_inference_scope(self, editorial_scorer,
                                 regression_cases):
        case = _find_case(regression_cases,
                          "broad_llm_inference_cache")
        rec = _score_case(editorial_scorer, case)
        assert rec.scope_bucket == "systems"

    def test_memory_paper_categories(self, editorial_scorer,
                                     regression_cases):
        case = _find_case(regression_cases,
                          "direct_memory_paper")
        rec = _score_case(editorial_scorer, case)
        # Should have high memory similarity
        assert rec.sim_memory > 0.3

    def test_broad_architecture_domain(self, editorial_scorer,
                                       regression_cases):
        case = _find_case(regression_cases,
                          "broad_architecture_weak_memory")
        rec = _score_case(editorial_scorer, case)
        assert rec.domain_bucket == "llm"


class TestRelativeScoring:
    """Test that scores are ordered correctly across cases."""

    def test_memory_paper_high_memory_score(
            self, editorial_scorer, regression_cases):
        mem_case = _find_case(regression_cases,
                              "direct_memory_paper")
        opt_case = _find_case(regression_cases,
                              "generic_optimizer_theory")
        mem_rec = _score_case(editorial_scorer, mem_case)
        opt_rec = _score_case(editorial_scorer, opt_case)
        assert mem_rec.memory_score > opt_rec.memory_score

    def test_broad_architecture_high_public_score(
            self, editorial_scorer, regression_cases):
        arch_case = _find_case(regression_cases,
                               "broad_architecture_weak_memory")
        narrow_case = _find_case(regression_cases,
                                 "narrow_image_restoration")
        arch_rec = _score_case(editorial_scorer, arch_case)
        narrow_rec = _score_case(editorial_scorer, narrow_case)
        assert arch_rec.public_interest_score > \
            narrow_rec.public_interest_score

    def test_narrow_domain_penalized(self, editorial_scorer,
                                     regression_cases):
        narrow_case = _find_case(regression_cases,
                                 "narrow_image_restoration")
        cache_case = _find_case(regression_cases,
                                "broad_llm_inference_cache")
        narrow_rec = _score_case(editorial_scorer, narrow_case)
        cache_rec = _score_case(editorial_scorer, cache_case)
        assert narrow_rec.max_axis_score < \
            cache_rec.max_axis_score

    def test_cache_paper_higher_than_optimizer(
            self, editorial_scorer, regression_cases):
        cache_case = _find_case(regression_cases,
                                "broad_llm_inference_cache")
        opt_case = _find_case(regression_cases,
                              "generic_optimizer_theory")
        cache_rec = _score_case(editorial_scorer, cache_case)
        opt_rec = _score_case(editorial_scorer, opt_case)
        assert cache_rec.max_axis_score > \
            opt_rec.max_axis_score

    def test_bandwidth_capacity_raises_memory_score(
            self, editorial_scorer):
        papers = [
            {
                "arxiv_id": "bw-low",
                "title": "ServeLite: Practical LLM Serving",
                "abstract": (
                    "We present an LLM serving runtime with improved "
                    "throughput and latency for production inference."
                ),
                "categories": ["cs.LG"],
                "published": "2026-03-01T00:00:00Z",
            },
            {
                "arxiv_id": "bw-high",
                "title": "ServeTier: CXL Offload for LLM Serving",
                "abstract": (
                    "We present an LLM serving runtime with CXL memory "
                    "pooling, DRAM offload, SSD spillover, interconnect "
                    "bandwidth management, and lower data movement costs."
                ),
                "categories": ["cs.LG"],
                "published": "2026-03-01T00:00:00Z",
            },
        ]
        scored = {
            rec.arxiv_id: rec for rec in editorial_scorer.score_papers(papers)
        }

        assert scored["bw-high"].bandwidth_capacity > 0.0
        assert scored["bw-high"].memory_score > scored["bw-low"].memory_score


class TestEditorialFilters:
    def test_narrow_domain_flag_image_restoration(
            self, editorial_scorer, regression_cases):
        case = _find_case(regression_cases,
                          "narrow_image_restoration")
        rec = _score_case(editorial_scorer, case)
        assert rec.narrow_domain_flag is True
        # Negative similarity should be relatively high
        assert rec.sim_negative > 0.3

    def test_cache_paper_not_narrow(self, editorial_scorer,
                                    regression_cases):
        case = _find_case(regression_cases,
                          "broad_llm_inference_cache")
        rec = _score_case(editorial_scorer, case)
        assert rec.narrow_domain_flag is False


class TestShortlist:
    def test_shortlist_respects_min_threshold(
            self, editorial_scorer, regression_cases):
        papers = [case["paper"] for case in regression_cases]
        records = editorial_scorer.score_papers(papers)
        shortlist = editorial_scorer.select_shortlist(records)
        min_max_axis = 0.15
        for rec in shortlist:
            assert rec.max_axis_score >= min_max_axis

    def test_shortlist_reserves_memory_representation(
            self, editorial_scorer):
        editorial_scorer.weights["shortlist"] = {
            "size": 4,
            "min_max_axis": 0.15,
            "memory_pool_size": 2,
            "memory_min_score": 0.25,
        }
        records = [
            PaperRecord(arxiv_id="pub-1", max_axis_score=0.95,
                        public_interest_score=0.95, memory_score=0.08,
                        bridge_score=0.08, quality_score=0.5,
                        novelty_score=0.5),
            PaperRecord(arxiv_id="pub-2", max_axis_score=0.92,
                        public_interest_score=0.92, memory_score=0.07,
                        bridge_score=0.07, quality_score=0.5,
                        novelty_score=0.5),
            PaperRecord(arxiv_id="pub-3", max_axis_score=0.89,
                        public_interest_score=0.89, memory_score=0.06,
                        bridge_score=0.06, quality_score=0.5,
                        novelty_score=0.5),
            PaperRecord(arxiv_id="mem-1", max_axis_score=0.41,
                        public_interest_score=0.20, memory_score=0.41,
                        bridge_score=0.20, quality_score=0.6,
                        novelty_score=0.6),
            PaperRecord(arxiv_id="mem-2", max_axis_score=0.37,
                        public_interest_score=0.22, memory_score=0.37,
                        bridge_score=0.22, quality_score=0.6,
                        novelty_score=0.6),
        ]

        shortlist = editorial_scorer.select_shortlist(records)
        ids = {rec.arxiv_id for rec in shortlist}

        assert ids == {"pub-1", "pub-2", "mem-1", "mem-2"}

    def test_cross_category_memory_paper_not_starved(
            self, editorial_scorer, regression_cases):
        editorial_scorer.weights["shortlist"] = {
            "size": 3,
            "min_max_axis": 0.15,
            "memory_pool_size": 1,
            "memory_min_score": 0.25,
        }
        papers = [
            _find_case(regression_cases, "broad_architecture_weak_memory")["paper"],
            _find_case(regression_cases, "broad_llm_inference_cache")["paper"],
            _find_case(regression_cases, "cross_category_memory_systems")["paper"],
        ]

        records = editorial_scorer.score_papers(papers)
        shortlist = editorial_scorer.select_shortlist(records)
        ids = {rec.arxiv_id for rec in shortlist}

        assert "9999.00006" in ids


# --- Helpers ---

def _find_case(cases, name):
    for c in cases:
        if c["name"] == name:
            return c
    raise ValueError(f"Regression case '{name}' not found")


def _score_case(scorer, case):
    """Score a single regression case and return its PaperRecord."""
    papers = [case["paper"]]
    records = scorer.score_papers(papers)
    return records[0]
