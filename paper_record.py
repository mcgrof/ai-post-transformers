"""Normalized paper record for the two-lens editorial queue.

PaperRecord is the canonical data structure flowing through the
editorial scoring pipeline. It normalizes fields from all sources
(arXiv, HF Daily, Semantic Scholar, GitHub Issues) into a single
schema with identity, source signals, taxonomy, similarity scores,
feature scores, penalties, composites, and LLM review fields.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class PaperRecord:
    # Identity
    arxiv_id: str = ""
    title: str = ""
    abstract: str = ""
    authors: List[str] = field(default_factory=list)
    published_at: str = ""
    categories: List[str] = field(default_factory=list)
    url: str = ""
    code_url: str = ""

    # Source signals
    github_submission_flag: bool = False
    hf_trending_flag: bool = False
    citation_count: int = 0
    influential_citation_count: int = 0

    # Taxonomy (set by first pass)
    scope_bucket: str = ""      # foundation|systems|architecture|...
    domain_bucket: str = ""     # llm|multimodal|vision|audio|...
    paper_type: str = ""        # theory|empirical|systems|benchmark|survey|application
    narrow_domain_flag: bool = False

    # Similarities (set by first pass)
    sim_public: float = 0.0
    sim_memory: float = 0.0
    sim_negative: float = 0.0

    # Feature scores (set by first pass)
    broad_relevance: float = 0.0
    momentum: float = 0.0
    teachability: float = 0.0
    novelty_score: float = 0.0
    evidence_score: float = 0.0
    direct_memory_relevance: float = 0.0
    systems_leverage: float = 0.0
    deployment_proximity: float = 0.0
    memory_adjacent_future_value: float = 0.0
    bandwidth_capacity: float = 0.0
    transferability_score: float = 0.0
    clarity: float = 0.0
    reproducibility: float = 0.0

    # Composite scores (set by first pass, refined by second pass)
    public_interest_score: float = 0.0
    memory_score: float = 0.0
    quality_score: float = 0.0
    bridge_score: float = 0.0
    max_axis_score: float = 0.0

    # Penalties
    fatigue_penalty: float = 0.0
    negative_profile_penalty: float = 0.0

    # LLM review (set by second pass)
    badges: List[str] = field(default_factory=list)
    status: str = ""            # Cover now|Monitor|Deferred this cycle|Out of scope
    why_now: str = ""
    why_not_higher: str = ""
    downgrade_reasons: List[str] = field(default_factory=list)
    what_would_raise_priority: str = ""
    one_sentence_episode_hook: str = ""

    # Internal (not serialized)
    embedding: Optional[object] = field(default=None, repr=False)
    source: str = "digest"
    added: str = ""
    issue_number: Optional[int] = None

    @classmethod
    def from_paper_dict(cls, d):
        """Convert a source paper dict into a PaperRecord.

        Handles the varying field names across arXiv, HF Daily,
        Semantic Scholar, and GitHub Issues sources.
        """
        rec = cls()
        rec.arxiv_id = d.get("arxiv_id", "")
        rec.title = d.get("title", "")
        rec.abstract = d.get("abstract", "")
        rec.authors = d.get("authors", [])
        rec.published_at = d.get("published", "")
        rec.categories = d.get("categories", [])
        rec.url = d.get("arxiv_url",
                        f"http://arxiv.org/abs/{rec.arxiv_id}")
        rec.code_url = d.get("code_url", "")
        rec.github_submission_flag = (
            d.get("source") == "github-issue")
        rec.hf_trending_flag = bool(d.get("hf_daily", False))
        rec.citation_count = d.get("citation_count", 0)
        rec.influential_citation_count = d.get(
            "influential_citation_count", 0)
        rec.source = d.get("source", "digest")
        rec.added = d.get("added", "")
        rec.issue_number = d.get("issue_number")
        return rec

    def to_dict(self):
        """Serialize to a plain dict, excluding the embedding vector."""
        d = asdict(self)
        d.pop("embedding", None)
        return d

    def score_breakdown(self):
        """Human-readable multiline score breakdown."""
        lines = [
            f"Paper: {self.title}",
            f"  arxiv_id: {self.arxiv_id}",
            f"  taxonomy: scope={self.scope_bucket} "
            f"domain={self.domain_bucket} type={self.paper_type}",
            f"  similarities: pub={self.sim_public:.3f} "
            f"mem={self.sim_memory:.3f} neg={self.sim_negative:.3f}",
            f"  public_interest: {self.public_interest_score:.3f} "
            f"(broad={self.broad_relevance:.3f} "
            f"momentum={self.momentum:.3f} "
            f"teach={self.teachability:.3f} "
            f"novelty={self.novelty_score:.3f} "
            f"evidence={self.evidence_score:.3f})",
            f"  memory: {self.memory_score:.3f} "
            f"(direct={self.direct_memory_relevance:.3f} "
            f"sys={self.systems_leverage:.3f} "
            f"deploy={self.deployment_proximity:.3f} "
            f"adjacent={self.memory_adjacent_future_value:.3f} "
            f"bw={self.bandwidth_capacity:.3f})",
            f"  quality: {self.quality_score:.3f} "
            f"(evidence={self.evidence_score:.3f} "
            f"transfer={self.transferability_score:.3f} "
            f"clarity={self.clarity:.3f} "
            f"repro={self.reproducibility:.3f})",
            f"  composites: bridge={self.bridge_score:.3f} "
            f"max_axis={self.max_axis_score:.3f}",
            f"  penalties: fatigue={self.fatigue_penalty:.3f} "
            f"negative={self.negative_profile_penalty:.3f}",
        ]
        if self.badges:
            lines.append(f"  badges: {', '.join(self.badges)}")
        if self.status:
            lines.append(f"  status: {self.status}")
        if self.why_now:
            lines.append(f"  why_now: {self.why_now}")
        if self.one_sentence_episode_hook:
            lines.append(
                f"  hook: {self.one_sentence_episode_hook}")
        return "\n".join(lines)
