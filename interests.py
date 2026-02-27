"""Interest profile embedding and paper scoring."""

import re
import sys
import numpy as np
from sentence_transformers import SentenceTransformer


class InterestScorer:
    def __init__(self, config, podcasted_ids=None):
        model_name = config.get("embedding_model", "all-MiniLM-L6-v2")
        print(f"[Scorer] Loading model {model_name}...", file=sys.stderr)
        self.model = SentenceTransformer(model_name)
        self.config = config
        self.podcasted_ids = podcasted_ids or set()
        self._build_profile(config)

    def _build_profile(self, config):
        """Pre-embed interest profile vectors."""
        interests = config.get("interests", {})
        primary = interests.get("primary", [])
        secondary = interests.get("secondary", [])

        self.primary_embeddings = self.model.encode(primary, normalize_embeddings=True)
        self.secondary_embeddings = self.model.encode(secondary, normalize_embeddings=True)

        scoring = config.get("scoring", {})
        self.secondary_weight = scoring.get("secondary_interest_weight", 0.5)
        self.hf_boost = scoring.get("hf_daily_boost", 0.15)
        self.citation_boost = scoring.get("citation_velocity_boost", 0.10)

        kw = config.get("keyword_boosts", {})
        self.kw_high = [w.lower() for w in kw.get("high", [])]
        self.kw_medium = [w.lower() for w in kw.get("medium", [])]
        self.kw_low = [w.lower() for w in kw.get("low", [])]
        self.kw_boost_high = scoring.get("keyword_boost_high", 0.12)
        self.kw_boost_medium = scoring.get("keyword_boost_medium", 0.06)
        self.kw_boost_low = scoring.get("keyword_boost_low", 0.03)
        self.podcast_penalty = scoring.get("podcast_penalty", 0.90)

        print(f"[Scorer] Profile: {len(primary)} primary, "
              f"{len(secondary)} secondary interests", file=sys.stderr)
        if self.podcasted_ids:
            print(f"[Scorer] {len(self.podcasted_ids)} papers already podcasted (will be penalized)", file=sys.stderr)

    def score_paper(self, paper):
        """Score a single paper against the interest profile.

        Returns (score, reason) tuple.
        """
        text = f"{paper['title']}. {paper.get('abstract', '')}"
        embedding = self.model.encode([text], normalize_embeddings=True)[0]

        # Cosine similarity against primary interests
        primary_sims = embedding @ self.primary_embeddings.T
        best_primary_idx = int(np.argmax(primary_sims))
        best_primary_sim = float(primary_sims[best_primary_idx])

        # Cosine similarity against secondary interests
        secondary_sim = 0.0
        if len(self.secondary_embeddings) > 0:
            secondary_sims = embedding @ self.secondary_embeddings.T
            secondary_sim = float(np.max(secondary_sims)) * self.secondary_weight

        base_score = max(best_primary_sim, secondary_sim)

        # Keyword boosting
        text_lower = text.lower()
        kw_boost = 0.0
        matched_kw = []
        for kw in self.kw_high:
            if kw.lower() in text_lower:
                kw_boost += self.kw_boost_high
                matched_kw.append(kw)
        for kw in self.kw_medium:
            if kw.lower() in text_lower:
                kw_boost += self.kw_boost_medium
                matched_kw.append(kw)
        for kw in self.kw_low:
            if kw.lower() in text_lower:
                kw_boost += self.kw_boost_low
                matched_kw.append(kw)

        # HF Daily boost
        hf_boost = self.hf_boost if paper.get("hf_daily") else 0.0

        # Citation velocity boost (normalize: log scale, cap at boost max)
        cit_vel = paper.get("citation_velocity", 0.0)
        cit_boost = 0.0
        if cit_vel > 0:
            cit_boost = min(self.citation_boost, self.citation_boost * np.log1p(cit_vel) / 5.0)

        total = base_score + kw_boost + hf_boost + cit_boost

        # Penalize papers already covered in podcasts
        podcasted = paper.get("arxiv_id") in self.podcasted_ids
        if podcasted:
            total *= (1.0 - self.podcast_penalty)

        # Build reason string
        primary_interests = self.config.get("interests", {}).get("primary", [])
        reason_parts = []
        if best_primary_sim > secondary_sim:
            interest_text = primary_interests[best_primary_idx] if best_primary_idx < len(primary_interests) else "primary interest"
            # Shorten the interest text
            interest_short = interest_text.split(",")[0].strip()
            reason_parts.append(f"matches '{interest_short}' ({best_primary_sim:.2f})")
        else:
            reason_parts.append(f"secondary interest match ({secondary_sim:.2f})")

        if matched_kw:
            reason_parts.append(f"keywords: {', '.join(matched_kw[:3])}")
        if hf_boost > 0:
            reason_parts.append("HF trending")
        if cit_boost > 0:
            reason_parts.append(f"cited ({int(paper.get('citation_count', 0))})")
        if podcasted:
            reason_parts.append("already podcasted")

        reason = "; ".join(reason_parts)
        return round(total, 4), reason

    def score_papers(self, papers):
        """Score all papers and return them sorted by score descending."""
        print(f"[Scorer] Scoring {len(papers)} papers...", file=sys.stderr)

        # Batch encode all papers at once for efficiency
        texts = [f"{p['title']}. {p.get('abstract', '')}" for p in papers]
        embeddings = self.model.encode(texts, normalize_embeddings=True,
                                        show_progress_bar=False, batch_size=64)

        for i, paper in enumerate(papers):
            emb = embeddings[i]

            primary_sims = emb @ self.primary_embeddings.T
            best_primary_idx = int(np.argmax(primary_sims))
            best_primary_sim = float(primary_sims[best_primary_idx])

            secondary_sim = 0.0
            if len(self.secondary_embeddings) > 0:
                secondary_sims = emb @ self.secondary_embeddings.T
                secondary_sim = float(np.max(secondary_sims)) * self.secondary_weight

            base_score = max(best_primary_sim, secondary_sim)

            text_lower = texts[i].lower()
            kw_boost = 0.0
            matched_kw = []
            for kw in self.kw_high:
                if kw.lower() in text_lower:
                    kw_boost += self.kw_boost_high
                    matched_kw.append(kw)
            for kw in self.kw_medium:
                if kw.lower() in text_lower:
                    kw_boost += self.kw_boost_medium
                    matched_kw.append(kw)
            for kw in self.kw_low:
                if kw.lower() in text_lower:
                    kw_boost += self.kw_boost_low
                    matched_kw.append(kw)

            hf_boost = self.hf_boost if paper.get("hf_daily") else 0.0

            cit_vel = paper.get("citation_velocity", 0.0)
            cit_boost = 0.0
            if cit_vel > 0:
                cit_boost = min(self.citation_boost,
                                self.citation_boost * np.log1p(cit_vel) / 5.0)

            total = base_score + kw_boost + hf_boost + cit_boost

            # Penalize papers already covered in podcasts
            podcasted = paper.get("arxiv_id") in self.podcasted_ids
            if podcasted:
                total *= (1.0 - self.podcast_penalty)

            primary_interests = self.config.get("interests", {}).get("primary", [])
            reason_parts = []
            if best_primary_sim > secondary_sim:
                interest_text = primary_interests[best_primary_idx] if best_primary_idx < len(primary_interests) else "primary interest"
                interest_short = interest_text.split(",")[0].strip()
                reason_parts.append(f"matches '{interest_short}' ({best_primary_sim:.2f})")
            else:
                reason_parts.append(f"secondary interest match ({secondary_sim:.2f})")

            if matched_kw:
                reason_parts.append(f"keywords: {', '.join(matched_kw[:3])}")
            if hf_boost > 0:
                reason_parts.append("HF trending")
            if cit_boost > 0:
                reason_parts.append(f"cited ({int(paper.get('citation_count', 0))})")
            if podcasted:
                reason_parts.append("already podcasted")

            paper["score"] = round(total, 4)
            paper["score_reason"] = "; ".join(reason_parts)

        papers.sort(key=lambda p: p["score"], reverse=True)
        print(f"[Scorer] Top score: {papers[0]['score']:.3f}" if papers else "[Scorer] No papers", file=sys.stderr)
        return papers
