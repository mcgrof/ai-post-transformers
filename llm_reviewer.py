"""Second-pass LLM editorial reviewer for the paper queue.

Runs parallel LLM calls on the shortlisted papers using the
7-question rubric. Uses the existing llm_backend.py infrastructure
(get_llm_backend, llm_call) so it works with claude-cli, openai,
or anthropic backends transparently.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm_backend import get_llm_backend, llm_call
from editorial_scorer import clamp


def _log(tag, msg, color="cyan"):
    print(f"{tag} {msg}", file=sys.stderr)


REVIEW_PROMPT_TEMPLATE = """\
You are an editorial reviewer for the AI Post Transformers podcast.
This podcast covers AI research under two declared editorial lenses:

1. Public AI Interest: papers that matter to a broad AI audience
   and make strong educational episodes.
2. Memory/Storage First-Class AI: papers about memory bandwidth,
   capacity, KV cache, data movement, offload, quantization,
   locality, tiering, paging, interconnect, or scheduling in AI.

A paper can score high on one, both, or neither lens.
This is editorial triage, not peer review.

PAPER:
Title: {title}
Authors: {authors}
Categories: {categories}
Abstract: {abstract}

FIRST-PASS SCORES (from embedding similarity and heuristics):
  public_interest_score: {public_interest_score:.3f}
  memory_score: {memory_score:.3f}
  quality_score: {quality_score:.3f}
  scope: {scope_bucket}, domain: {domain_bucket}, type: {paper_type}
  sim_public: {sim_public:.3f}, sim_memory: {sim_memory:.3f}
  sim_negative: {sim_negative:.3f}

Answer these 7 questions, then produce your JSON verdict:

1. Would a broad AI audience likely care within 30-90 days?
2. Does this paper change how people build, train, serve, or
   evaluate modern AI systems?
3. Is the memory/storage connection direct, adjacent, or absent?
4. Are claims supported on realistic workloads, models, or hardware?
5. Is this general, or a narrow task paper wearing a transformer
   costume?
6. Genuinely new relative to recent episodes, or a near-duplicate?
7. Good episode potential, or just a benchmark result?

IMPORTANT GUIDELINES:
- Be skeptical of keyword overlap. "GPU memory" in a medical
  imaging paper does not make it a memory paper.
- Optimizer or theory papers without clear large-model or systems
  relevance: usually Deferred this cycle.
- Narrow application papers: usually Out of scope unless the
  general method clearly transfers.
- Memory-adjacent papers can score well on the memory lens if they
  create useful predictive signals for scheduling, prefetch,
  tiering, or cache reuse.

Output ONLY valid JSON with exactly these fields:
{{
  "public_interest_score_adjustment": <float between -0.3 and 0.3>,
  "memory_score_adjustment": <float between -0.3 and 0.3>,
  "evidence_score_adjustment": <float between -0.3 and 0.3>,
  "transferability_score_adjustment": <float between -0.3 and 0.3>,
  "badges": [<list of applicable badges from: "Public AI",
    "Memory/Storage Core", "Memory/Storage Adjacent", "Bridge",
    "Systems", "Theory", "Hardware", "Training", "Inference",
    "Application">],
  "status": "<one of: Cover now, Monitor, Deferred this cycle, Out of scope>",
  "why_now": "<1-2 sentences: why this paper matters now>",
  "why_not_higher": "<1-2 sentences: what limits its ranking>",
  "downgrade_reasons": [<list of short reasons if downgraded>],
  "what_would_raise_priority": "<1 sentence>",
  "one_sentence_episode_hook": "<compelling podcast episode hook>"
}}
"""


class LLMReviewer:
    def __init__(self, config):
        self.config = config
        self.backend = get_llm_backend(config)
        self.model = config.get("podcast", {}).get(
            "analysis_model", "sonnet")
        self.workers = config.get("editorial", {}).get(
            "llm_workers", 4)

    def review_papers(self, records):
        """Run parallel LLM review on shortlisted PaperRecords.

        Returns the same records with LLM review fields populated.
        On failure for any paper, it keeps first-pass scores and
        gets status "Monitor" as a safe default.
        """
        _log("[LLMReview]",
             f"Reviewing {len(records)} papers "
             f"({self.workers} workers, "
             f"backend={self.backend['type']}, "
             f"model={self.model})...")

        results = {}
        with ThreadPoolExecutor(
                max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._review_one, rec): rec
                for rec in records
            }
            for fut in as_completed(futures):
                rec = futures[fut]
                try:
                    review = fut.result()
                    results[rec.arxiv_id] = review
                except Exception as e:
                    _log("[LLMReview]",
                         f"Failed for {rec.arxiv_id}: {e}")
                    results[rec.arxiv_id] = None

        # Apply results
        reviewed = 0
        for rec in records:
            review = results.get(rec.arxiv_id)
            if review is None:
                # Safe default on failure
                if not rec.status:
                    rec.status = "Monitor"
                continue
            self._apply_review(rec, review)
            reviewed += 1

        _log("[LLMReview]",
             f"Reviewed {reviewed}/{len(records)} papers")
        return records

    def _review_one(self, rec):
        """Send a single paper to the LLM for review."""
        authors_str = ", ".join(rec.authors[:5])
        if len(rec.authors) > 5:
            authors_str += " et al."

        prompt = REVIEW_PROMPT_TEMPLATE.format(
            title=rec.title,
            authors=authors_str,
            categories=", ".join(rec.categories),
            abstract=rec.abstract[:2000],
            public_interest_score=rec.public_interest_score,
            memory_score=rec.memory_score,
            quality_score=rec.quality_score,
            scope_bucket=rec.scope_bucket,
            domain_bucket=rec.domain_bucket,
            paper_type=rec.paper_type,
            sim_public=rec.sim_public,
            sim_memory=rec.sim_memory,
            sim_negative=rec.sim_negative,
        )

        return llm_call(
            self.backend, self.model, prompt,
            temperature=0.3, max_tokens=2000,
            json_mode=True)

    def _apply_review(self, rec, review):
        """Apply LLM review adjustments to a PaperRecord."""
        # Score adjustments (clamped to ±0.3, then final clamp)
        pub_adj = _clamp_adj(
            review.get("public_interest_score_adjustment", 0))
        mem_adj = _clamp_adj(
            review.get("memory_score_adjustment", 0))
        ev_adj = _clamp_adj(
            review.get("evidence_score_adjustment", 0))
        tr_adj = _clamp_adj(
            review.get("transferability_score_adjustment", 0))

        rec.public_interest_score = clamp(
            rec.public_interest_score + pub_adj)
        rec.memory_score = clamp(
            rec.memory_score + mem_adj)
        rec.evidence_score = clamp(
            rec.evidence_score + ev_adj)
        rec.transferability_score = clamp(
            rec.transferability_score + tr_adj)

        # Recompute composites after adjustments
        rec.bridge_score = min(
            rec.public_interest_score, rec.memory_score)
        rec.max_axis_score = max(
            rec.public_interest_score, rec.memory_score)

        # Editorial fields
        badges = review.get("badges", [])
        if isinstance(badges, list):
            rec.badges = badges
        rec.status = review.get("status", "Monitor")
        rec.why_now = review.get("why_now", "")
        rec.why_not_higher = review.get("why_not_higher", "")
        reasons = review.get("downgrade_reasons", [])
        if isinstance(reasons, list):
            rec.downgrade_reasons = reasons
        rec.what_would_raise_priority = review.get(
            "what_would_raise_priority", "")
        rec.one_sentence_episode_hook = review.get(
            "one_sentence_episode_hook", "")


def _clamp_adj(v):
    """Clamp an adjustment value to [-0.3, 0.3]."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(-0.3, min(0.3, v))
