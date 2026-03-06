"""First-pass editorial scorer for the two-lens paper queue.

Replaces the single InterestScorer with a transparent two-lens
system: Public AI Interest and Memory/Storage First-Class AI.
All weights are loaded from weights.yaml and editorial_lenses.yaml.
Seed sets for embedding profiles come from seed_sets/*.yaml.
"""

import re
import sys
import math
from pathlib import Path

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

from paper_record import PaperRecord


def _isatty():
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

_COLORS = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "cyan":    "\033[36m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "magenta": "\033[35m",
    "red":     "\033[31m",
    "blue":    "\033[34m",
}

def _c(color, text):
    if not _isatty():
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"

def _log(tag, msg, color="cyan"):
    print(f"{_c(color, tag)} {msg}", file=sys.stderr)


class EditorialScorer:
    def __init__(self, config, podcasted_ids=None,
                 covered_topic_texts=None):
        base = Path(__file__).parent

        # Load config files
        lenses_file = config.get("editorial", {}).get(
            "lenses_file", "editorial_lenses.yaml")
        weights_file = config.get("editorial", {}).get(
            "weights_file", "weights.yaml")
        seed_dir = config.get("editorial", {}).get(
            "seed_sets_dir", "seed_sets")

        with open(base / lenses_file) as f:
            self.lenses = yaml.safe_load(f)
        with open(base / weights_file) as f:
            self.weights = yaml.safe_load(f)

        # Load seed sets
        self.public_seeds = self._load_seeds(
            base / seed_dir / "public_interest_positive.yaml")
        self.memory_seeds = self._load_seeds(
            base / seed_dir / "memory_storage_positive.yaml")
        self.negative_seeds = self._load_seeds(
            base / seed_dir / "negative_out_of_scope.yaml")

        # Load embedding model
        model_name = config.get("embedding_model", "all-MiniLM-L6-v2")
        _log("[Editorial]", f"Loading model {model_name}...")
        self.model = SentenceTransformer(model_name)

        # Pre-embed seed sets
        self.public_embs = self.model.encode(
            self.public_seeds, normalize_embeddings=True)
        self.memory_embs = self.model.encode(
            self.memory_seeds, normalize_embeddings=True)
        self.negative_embs = self.model.encode(
            self.negative_seeds, normalize_embeddings=True)

        self.podcasted_ids = podcasted_ids or set()
        self.config = config

        # Pre-embed covered topics for novelty/fatigue
        self.covered_topic_embs = None
        if covered_topic_texts:
            self.covered_topic_embs = self.model.encode(
                list(covered_topic_texts),
                normalize_embeddings=True)

        # Build taxonomy helpers
        self.cat_scope_map = self.lenses.get(
            "arxiv_category_map", {}).get("scope", {})
        self.cat_domain_map = self.lenses.get(
            "arxiv_category_map", {}).get("domain", {})
        self.narrow_domains = set(
            d.lower() for d in self.lenses.get(
                "narrow_domain_penalty_list", []))

        _log("[Editorial]",
             f"Seeds: {_c('bold', str(len(self.public_seeds)))} public, "
             f"{_c('bold', str(len(self.memory_seeds)))} memory, "
             f"{_c('bold', str(len(self.negative_seeds)))} negative")

    def _load_seeds(self, path):
        with open(path) as f:
            data = yaml.safe_load(f)
        return [s.strip() for s in data.get("seeds", []) if s.strip()]

    def score_papers(self, papers):
        """Score all papers through the first-pass editorial pipeline.

        Returns a list of PaperRecord objects sorted by max_axis_score.
        """
        _log("[Editorial]",
             f"Scoring {_c('bold', str(len(papers)))} papers...")

        # Convert to PaperRecords
        records = [PaperRecord.from_paper_dict(p) for p in papers]

        # Batch embed
        texts = [f"{r.title}. {r.abstract}" for r in records]
        embeddings = self.model.encode(
            texts, normalize_embeddings=True,
            show_progress_bar=False, batch_size=64)

        for i, rec in enumerate(records):
            rec.embedding = embeddings[i]

        # Pipeline steps
        self._classify_taxonomy(records)
        self._apply_editorial_filters(records)
        self._compute_similarities(records, embeddings)
        self._compute_feature_scores(records)

        records.sort(key=lambda r: r.max_axis_score, reverse=True)
        if records:
            _log("[Editorial]",
                 f"Top max_axis: "
                 f"{_c('green', f'{records[0].max_axis_score:.3f}')} "
                 f"({records[0].title[:60]})")
        return records

    def _classify_taxonomy(self, records):
        """Classify scope_bucket, domain_bucket, paper_type."""
        for rec in records:
            rec.scope_bucket = self._infer_scope(rec)
            rec.domain_bucket = self._infer_domain(rec)
            rec.paper_type = self._infer_paper_type(rec)

    def _infer_scope(self, rec):
        # Try arXiv category mapping first
        for cat in rec.categories:
            if cat in self.cat_scope_map:
                return self.cat_scope_map[cat]

        text = f"{rec.title} {rec.abstract}".lower()
        scope_kw = {
            "inference": ["inference", "serving", "latency",
                          "throughput", "decoding"],
            "training": ["training", "fine-tun", "pretraining",
                         "gradient", "optimizer"],
            "architecture": ["architecture", "layer", "attention",
                             "module", "block design"],
            "systems": ["system", "distributed", "scheduling",
                        "cluster", "pipeline parallel"],
            "hardware": ["hardware", "gpu", "hbm", "accelerat",
                         "chip", "fpga", "asic"],
            "eval": ["evaluation", "evaluating", "leaderboard"],
            "benchmark": ["benchmark", "dataset", "testbed"],
            "survey": ["survey", "review", "overview",
                       "systematic review"],
        }
        for bucket, keywords in scope_kw.items():
            if any(kw in text for kw in keywords):
                return bucket
        return "foundation"

    def _infer_domain(self, rec):
        for cat in rec.categories:
            if cat in self.cat_domain_map:
                return self.cat_domain_map[cat]

        text = f"{rec.title} {rec.abstract}".lower()
        domain_kw = {
            "llm": ["language model", "llm", "gpt", "llama",
                     "chatbot", "text generation"],
            "multimodal": ["multimodal", "vision-language",
                           "image-text", "clip"],
            "vision": ["image", "visual", "object detection",
                       "segmentation", "computer vision"],
            "audio": ["speech", "audio", "voice", "asr", "tts"],
            "robotics": ["robot", "manipulation", "navigation",
                         "embodied"],
            "bio": ["protein", "genomic", "biological",
                    "molecular"],
            "medical": ["medical", "clinical", "pathology",
                        "radiology"],
            "graphics": ["rendering", "3d", "nerf", "mesh",
                         "point cloud"],
            "pde": ["pde", "differential equation", "physics-informed",
                    "fluid", "simulation"],
            "recommendation": ["recommendation", "click-through",
                               "collaborative filtering"],
        }
        for bucket, keywords in domain_kw.items():
            if any(kw in text for kw in keywords):
                return bucket
        return "other"

    def _infer_paper_type(self, rec):
        text = f"{rec.title} {rec.abstract}".lower()
        type_kw = {
            "survey": ["survey", "systematic review", "overview of",
                       "comprehensive review"],
            "benchmark": ["benchmark", "leaderboard", "testbed",
                          "evaluation suite"],
            "theory": ["proof", "theorem", "convergence",
                       "theoretical analysis", "provable",
                       "convergence guarantee"],
            "systems": ["system design", "implementation",
                        "cluster", "serving system",
                        "distributed system"],
            "application": ["application", "deploy", "case study",
                            "real-world"],
        }
        for ptype, keywords in type_kw.items():
            if any(kw in text for kw in keywords):
                return ptype
        return "empirical"

    def _apply_editorial_filters(self, records):
        """Flag narrow domain papers for downstream penalty."""
        for rec in records:
            text = f"{rec.title} {rec.abstract}".lower()
            for narrow in self.narrow_domains:
                if narrow in text:
                    rec.narrow_domain_flag = True
                    break
            # Also flag if domain is in known narrow set
            if rec.domain_bucket in ("medical", "bio", "graphics",
                                     "pde", "recommendation"):
                rec.narrow_domain_flag = True

    def _compute_similarities(self, records, embeddings):
        """Compute triple similarity against seed profiles."""
        # Matrix multiply: (N, dim) @ (S, dim).T -> (N, S)
        sim_pub = embeddings @ self.public_embs.T
        sim_mem = embeddings @ self.memory_embs.T
        sim_neg = embeddings @ self.negative_embs.T

        for i, rec in enumerate(records):
            rec.sim_public = float(np.max(sim_pub[i]))
            rec.sim_memory = float(np.max(sim_mem[i]))
            rec.sim_negative = float(np.max(sim_neg[i]))

    def _compute_feature_scores(self, records):
        """Compute all feature scores, composites, and penalties."""
        w = self.weights
        boosts = w.get("boosts", {})
        penalties = w.get("penalties", {})
        w_pub = w.get("public_interest", {})
        w_mem = w.get("memory", {})
        w_qual = w.get("quality", {})

        for rec in records:
            text = f"{rec.title} {rec.abstract}".lower()

            # --- Individual feature scores ---

            # broad_relevance: driven by public similarity
            rec.broad_relevance = rec.sim_public

            # momentum: HF trending + GitHub + citation velocity
            momentum_parts = []
            if rec.hf_trending_flag:
                momentum_parts.append(boosts.get("hf_trending", 0.12))
            if rec.github_submission_flag:
                momentum_parts.append(
                    boosts.get("github_submission", 0.08))
            cit_vel = (rec.citation_count * 0.3
                       + rec.influential_citation_count * 2.0)
            if cit_vel > 0:
                cit_norm = min(1.0, math.log1p(cit_vel) / 5.0)
                momentum_parts.append(
                    cit_norm * boosts.get(
                        "citation_velocity_max", 0.10))
            rec.momentum = min(1.0, sum(momentum_parts) / 0.30) \
                if momentum_parts else 0.0

            # teachability: heuristic from scope and abstract
            teach = 0.5  # base
            if rec.scope_bucket in ("foundation", "architecture",
                                    "systems", "inference",
                                    "training"):
                teach += 0.15
            if len(rec.abstract) > 200:
                teach += 0.1
            if any(kw in text for kw in [
                    "we show", "we demonstrate", "we propose",
                    "key insight", "main contribution"]):
                teach += 0.1
            rec.teachability = min(1.0, teach)

            # novelty: 1 - max_sim(paper, covered topics)
            if (self.covered_topic_embs is not None
                    and len(self.covered_topic_embs) > 0
                    and rec.embedding is not None):
                topic_sims = rec.embedding @ \
                    self.covered_topic_embs.T
                rec.novelty_score = max(
                    0.0, 1.0 - float(np.max(topic_sims)))
            else:
                rec.novelty_score = 0.7  # default if no history

            # evidence: citations + code + paper_type signals
            ev = 0.3  # base
            if rec.citation_count > 0:
                ev += min(0.3, math.log1p(rec.citation_count) / 10.0)
            if rec.code_url:
                ev += 0.15
            if rec.paper_type in ("empirical", "systems",
                                  "benchmark"):
                ev += 0.1
            rec.evidence_score = min(1.0, ev)

            # direct_memory_relevance: driven by memory similarity
            rec.direct_memory_relevance = rec.sim_memory

            # systems_leverage: keyword match
            sys_kw = ["system", "serving", "distributed",
                      "scheduling", "pipeline", "runtime",
                      "kernel", "operator", "compiler"]
            sys_hits = sum(1 for kw in sys_kw if kw in text)
            rec.systems_leverage = min(
                1.0, sys_hits * 0.15 + 0.1)

            # deployment_proximity: scope-driven
            if rec.scope_bucket in ("inference", "training",
                                    "systems"):
                rec.deployment_proximity = 0.7
            elif rec.scope_bucket in ("architecture", "hardware"):
                rec.deployment_proximity = 0.5
            else:
                rec.deployment_proximity = 0.2

            # memory_adjacent_future_value: keywords
            mem_adj_kw = ["speculative decoding", "spec decod",
                          "routing", "prefetch", "prefill",
                          "cache reuse", "token prediction",
                          "execution forecast", "dynamic routing"]
            adj_hits = sum(1 for kw in mem_adj_kw if kw in text)
            rec.memory_adjacent_future_value = min(
                1.0, adj_hits * 0.25)

            # bandwidth_capacity: keywords
            bw_kw = ["hbm", "dram", "cxl", "bandwidth",
                     "memory capacity", "data movement",
                     "interconnect", "nvme", "ssd", "offload",
                     "memory wall", "memory footprint"]
            bw_hits = sum(1 for kw in bw_kw if kw in text)
            rec.bandwidth_capacity = min(1.0, bw_hits * 0.2)

            # transferability: general method detection
            transfer = 0.3  # base
            if rec.domain_bucket in ("llm", "multimodal", "other"):
                transfer += 0.2
            if any(kw in text for kw in [
                    "general", "any model", "model-agnostic",
                    "drop-in", "plug-in", "framework"]):
                transfer += 0.2
            if not rec.narrow_domain_flag:
                transfer += 0.1
            rec.transferability_score = min(1.0, transfer)

            # clarity: heuristic
            rec.clarity = min(1.0, 0.5 + (
                0.1 if "we propose" in text else 0.0) + (
                0.1 if len(rec.abstract) > 300 else 0.0) + (
                0.1 if rec.paper_type != "theory" else 0.0))

            # reproducibility
            rec.reproducibility = min(1.0,
                0.3 + (0.3 if rec.code_url else 0.0) + (
                0.2 if rec.paper_type in (
                    "empirical", "systems", "benchmark")
                else 0.0))

            # --- Composite scores ---
            rec.public_interest_score = clamp(
                w_pub.get("broad_relevance", 0.30)
                    * rec.broad_relevance
                + w_pub.get("momentum", 0.20) * rec.momentum
                + w_pub.get("teachability", 0.20)
                    * rec.teachability
                + w_pub.get("novelty", 0.15) * rec.novelty_score
                + w_pub.get("evidence", 0.15) * rec.evidence_score
            )

            rec.memory_score = clamp(
                w_mem.get("direct_memory_relevance", 0.35)
                    * rec.direct_memory_relevance
                + w_mem.get("systems_leverage", 0.20)
                    * rec.systems_leverage
                + w_mem.get("deployment_proximity", 0.15)
                    * rec.deployment_proximity
                + w_mem.get("evidence", 0.15) * rec.evidence_score
                + w_mem.get("memory_adjacent_future_value", 0.15)
                    * rec.memory_adjacent_future_value
            )

            rec.quality_score = clamp(
                w_qual.get("evidence", 0.40) * rec.evidence_score
                + w_qual.get("transferability", 0.25)
                    * rec.transferability_score
                + w_qual.get("clarity", 0.20) * rec.clarity
                + w_qual.get("reproducibility", 0.15)
                    * rec.reproducibility
            )

            # --- Penalties ---

            # Fatigue: similarity to recent episodes
            if (self.covered_topic_embs is not None
                    and len(self.covered_topic_embs) > 0
                    and rec.embedding is not None):
                topic_sims = rec.embedding @ \
                    self.covered_topic_embs.T
                max_topic_sim = float(np.max(topic_sims))
                if max_topic_sim > 0.75:
                    rec.fatigue_penalty = min(
                        penalties.get("fatigue_max", 0.25),
                        (max_topic_sim - 0.75) * 1.0)

            # Negative profile penalty
            if rec.sim_negative > 0.5:
                rec.negative_profile_penalty = min(
                    penalties.get("negative_profile_max", 0.30),
                    (rec.sim_negative - 0.5) * 0.6)

            # Narrow domain penalty
            if rec.narrow_domain_flag and \
                    rec.transferability_score < 0.5:
                narrow_pen = penalties.get("narrow_domain", 0.20)
                rec.public_interest_score = clamp(
                    rec.public_interest_score - narrow_pen)
                rec.memory_score = clamp(
                    rec.memory_score - narrow_pen)

            # Apply penalties to composites
            total_penalty = (rec.fatigue_penalty
                             + rec.negative_profile_penalty)
            if total_penalty > 0:
                rec.public_interest_score = clamp(
                    rec.public_interest_score - total_penalty)
                rec.memory_score = clamp(
                    rec.memory_score - total_penalty)

            # Podcasted penalty (hard)
            if rec.arxiv_id in self.podcasted_ids:
                rec.public_interest_score *= 0.1
                rec.memory_score *= 0.1

            # Final composites
            rec.bridge_score = min(
                rec.public_interest_score, rec.memory_score)
            rec.max_axis_score = max(
                rec.public_interest_score, rec.memory_score)

    def select_shortlist(self, records):
        """Select top papers for second-pass LLM review.

        Sorted by: max_axis + bridge_bonus * bridge + 0.2 * quality
        + 0.1 * novelty.
        """
        bridge_bonus = self.weights.get(
            "boosts", {}).get("bridge_bonus", 0.10)
        shortlist_cfg = self.weights.get("shortlist", {})
        size = shortlist_cfg.get("size", 100)
        min_max_axis = shortlist_cfg.get("min_max_axis", 0.15)

        # Filter out papers below minimum threshold
        eligible = [r for r in records
                    if r.max_axis_score >= min_max_axis]

        # Sort by composite ranking signal
        eligible.sort(
            key=lambda r: (
                r.max_axis_score
                + bridge_bonus * r.bridge_score
                + 0.2 * r.quality_score
                + 0.1 * r.novelty_score),
            reverse=True)

        shortlist = eligible[:size]
        _log("[Editorial]",
             f"Shortlist: {_c('bold', str(len(shortlist)))} papers "
             f"({_c('dim', f'from {len(eligible)} eligible')})")
        return shortlist


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))
