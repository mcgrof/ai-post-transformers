"""Tests for the second-pass LLM reviewer (mocked).

These tests verify that LLMReviewer correctly applies score
adjustments, badges, and statuses from LLM responses without
making actual API calls.
"""

from unittest.mock import patch, MagicMock

import pytest
from paper_record import PaperRecord
from llm_reviewer import LLMReviewer, _clamp_adj


@pytest.fixture
def mock_config():
    return {
        "podcast": {
            "llm_backend": "claude-cli",
            "analysis_model": "sonnet",
        },
        "editorial": {
            "llm_workers": 2,
        },
    }


@pytest.fixture
def sample_record():
    rec = PaperRecord(
        arxiv_id="2401.12345",
        title="Test Paper on KV Cache Optimization",
        abstract="We propose a new method for KV cache management.",
        authors=["Alice", "Bob"],
        categories=["cs.LG"],
        published_at="2026-01-01T00:00:00Z",
        url="http://arxiv.org/abs/2401.12345",
        scope_bucket="systems",
        domain_bucket="llm",
        paper_type="empirical",
        sim_public=0.5,
        sim_memory=0.7,
        sim_negative=0.1,
        public_interest_score=0.4,
        memory_score=0.6,
        quality_score=0.5,
        bridge_score=0.4,
        max_axis_score=0.6,
    )
    return rec


class TestClampAdj:
    def test_within_range(self):
        assert _clamp_adj(0.1) == 0.1

    def test_exceeds_max(self):
        assert _clamp_adj(0.5) == 0.3

    def test_below_min(self):
        assert _clamp_adj(-0.5) == -0.3

    def test_invalid_type(self):
        assert _clamp_adj("bad") == 0.0

    def test_none(self):
        assert _clamp_adj(None) == 0.0


class TestLLMReviewerApply:
    @patch("llm_reviewer.get_llm_backend")
    def test_positive_adjustment(self, mock_backend, mock_config,
                                 sample_record):
        mock_backend.return_value = {"type": "claude-cli"}
        reviewer = LLMReviewer(mock_config)

        review = {
            "public_interest_score_adjustment": 0.1,
            "memory_score_adjustment": 0.15,
            "evidence_score_adjustment": 0.05,
            "transferability_score_adjustment": 0.0,
            "badges": ["Memory/Storage Core", "Systems"],
            "status": "Cover now",
            "why_now": "Important for serving systems.",
            "why_not_higher": "Evaluation limited to one model.",
            "downgrade_reasons": [],
            "what_would_raise_priority": "Multi-model eval.",
            "one_sentence_episode_hook": "A new approach to KV cache.",
        }
        reviewer._apply_review(sample_record, review)

        assert sample_record.public_interest_score == pytest.approx(
            0.5, abs=0.01)
        assert sample_record.memory_score == pytest.approx(
            0.75, abs=0.01)
        assert sample_record.status == "Cover now"
        assert "Memory/Storage Core" in sample_record.badges
        assert sample_record.bridge_score == min(
            sample_record.public_interest_score,
            sample_record.memory_score)

    @patch("llm_reviewer.get_llm_backend")
    def test_negative_adjustment(self, mock_backend, mock_config,
                                 sample_record):
        mock_backend.return_value = {"type": "claude-cli"}
        reviewer = LLMReviewer(mock_config)

        review = {
            "public_interest_score_adjustment": -0.2,
            "memory_score_adjustment": -0.3,
            "evidence_score_adjustment": -0.1,
            "transferability_score_adjustment": -0.15,
            "badges": [],
            "status": "Deferred this cycle",
            "why_now": "",
            "why_not_higher": "Weak evidence.",
            "downgrade_reasons": ["Toy models only"],
            "what_would_raise_priority": "Real hardware eval.",
            "one_sentence_episode_hook": "",
        }
        reviewer._apply_review(sample_record, review)

        assert sample_record.public_interest_score == pytest.approx(
            0.2, abs=0.01)
        assert sample_record.memory_score == pytest.approx(
            0.3, abs=0.01)
        assert sample_record.status == "Deferred this cycle"

    @patch("llm_reviewer.get_llm_backend")
    def test_clamped_to_zero(self, mock_backend, mock_config):
        mock_backend.return_value = {"type": "claude-cli"}
        reviewer = LLMReviewer(mock_config)

        rec = PaperRecord(
            arxiv_id="test",
            public_interest_score=0.1,
            memory_score=0.05,
        )
        review = {
            "public_interest_score_adjustment": -0.3,
            "memory_score_adjustment": -0.3,
            "evidence_score_adjustment": 0,
            "transferability_score_adjustment": 0,
            "badges": [],
            "status": "Out of scope",
            "why_now": "",
            "why_not_higher": "",
            "downgrade_reasons": [],
            "what_would_raise_priority": "",
            "one_sentence_episode_hook": "",
        }
        reviewer._apply_review(rec, review)

        assert rec.public_interest_score >= 0.0
        assert rec.memory_score >= 0.0


class TestLLMReviewerFailure:
    @patch("llm_reviewer.get_llm_backend")
    @patch("llm_reviewer.llm_call")
    def test_failure_defaults_to_monitor(
            self, mock_llm, mock_backend, mock_config,
            sample_record):
        mock_backend.return_value = {"type": "claude-cli"}
        mock_llm.side_effect = RuntimeError("API timeout")

        reviewer = LLMReviewer(mock_config)
        results = reviewer.review_papers([sample_record])

        assert len(results) == 1
        assert results[0].status == "Monitor"
