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
