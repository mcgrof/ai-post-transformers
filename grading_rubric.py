"""Rubric for grading authenticity across 7 dimensions.

Each dimension is scored 0-4, with clear behavioral anchors.
Multiple reviewers (automated, LLM critic, human) score independently.
"""

from typing import Dict, List
from dataclasses import dataclass


@dataclass
class DimensionAnchor:
    """Behavioral anchors for a single dimension."""
    dimension: str
    score_0: str  # Worst case
    score_2: str  # Mixed
    score_4: str  # Best case


# Dimension anchors with behavioral descriptions
DIMENSION_ANCHORS = [
    DimensionAnchor(
        dimension="Evidence Contingency",
        score_0="Generic or fabricated statements; paper substitution barely matters. Dialogue could apply to any paper.",
        score_2="Paper-specific summary with some correct details, but hosts don't interpret evidence. Could work for related papers.",
        score_4="Claims and reactions tied to specific evidence, assumptions, or omissions. Different paper would produce substantively different discussion.",
    ),
    DimensionAnchor(
        dimension="Character-Conditioned Appraisal",
        score_0="Hosts interchangeable or distinguished only by catchphrases (e.g., 'As a pragmatist...'). Same reaction from all.",
        score_2="Different vocabulary and some different emphasis. Hosts take different positions but reason similarly.",
        score_4="Hosts select, value, doubt, and update on evidence according to distinct epistemic policies. Disagreement stems from different standards.",
    ),
    DimensionAnchor(
        dimension="Conversational Causality",
        score_0="Turns can be shuffled without damage. No responses, only adjacent monologues.",
        score_2="Some replies and reactive statements. Many turns could be reordered without breaking logic.",
        score_4="Each important turn changes live question, another host's position, or direction. Causality runs forward through the conversation.",
    ),
    DimensionAnchor(
        dimension="Belief Continuity",
        score_0="Positions reset; objections repeat after being answered. No memory between turns.",
        score_2="Positions remain consistent but mostly static. Hosts don't explicitly update.",
        score_4="Concessions, distinctions, confidence changes, unresolved objections persist. Hosts remember and reference prior exchanges.",
    ),
    DimensionAnchor(
        dimension="Agency/Asymmetry",
        score_0="Equal airtime, mandatory contributions, scheduled disagreement. Turn order is round-robin.",
        score_2="Some organic ownership and topic relevance, but also required contributions and filler.",
        score_4="Silence, deference, topic ownership, disagreement emerge from relevance. Participation follows from need.",
    ),
    DimensionAnchor(
        dimension="Anti-Caricature Coherence",
        score_0="Hosts deployed on command: Hal deploys, Ada proves, VERA narrates. Stereotypical across domains.",
        score_2="Recognizable but stereotyped. Same analytical patterns repeated regardless of paper type.",
        score_4="Hosts cross domains naturally while preserving distinct values and blind spots. Values apply to different content.",
    ),
    DimensionAnchor(
        dimension="Naturalism",
        score_0="Obviously templated or exposition disguised as dialogue. Unnatural phrasing, repetitive structures.",
        score_2="Mostly listenable with generated artifacts visible. Some turns feel natural, others obviously constructed.",
        score_4="Varied, economical, socially plausible. Sounds like thinking aloud, not reading a script.",
    ),
]


def get_anchor_for_dimension(dimension: str) -> DimensionAnchor:
    """Look up anchor descriptions for a dimension."""
    for anchor in DIMENSION_ANCHORS:
        if anchor.dimension == dimension:
            return anchor
    raise ValueError(f"Unknown dimension: {dimension}")


def score_evidence_contingency(text: str, audits: Dict) -> int:
    """Grade evidence contingency (0-4).

    Uses automated audit (paper substitutability check) + reviewer judgment.
    """
    # Automated check: count evidence references
    if "PAPER_SUBSTITUTABLE" in audits.get("failure_tags", []):
        # Reviewer should assign 0-2
        return 0
    # Without the substitutable tag, default to reviewer judgment
    # This would be filled in by LLM/human reviewer
    return 2  # placeholder


def score_character_contingency(text: str, audits: Dict, character_analysis: Dict = None) -> int:
    """Grade character-conditioned appraisal (0-4).

    Checks for:
    - Persona declarations (bad)
    - Distinct evidence appraisal patterns (good)
    - Character-specific reasoning (good)
    """
    tags = audits.get("failure_tags", [])
    score = 2  # Start at neutral

    if "PERSONA_DECLARATION" in tags:
        score -= 1  # Explicit "I'm X" is bad
    if "SPEAKER_INTERCHANGEABLE" in tags:
        score -= 1  # If any host could say it
    if "GENERIC_OPENER" in tags:
        score -= 0.5  # Generic phrasing hurts

    if character_analysis:
        if character_analysis.get("distinct_evidence_selections"):
            score += 1
        if character_analysis.get("different_appraisal_criteria"):
            score += 1

    return max(0, min(4, score))


def score_conversational_causality(lines: List[str], turn_analysis: Dict = None) -> int:
    """Grade conversational causality (0-4).

    Checks for:
    - Turn dependencies (does B respond to A?)
    - Topic continuity
    - Evidence of listening
    """
    score = 2  # Start at neutral

    if turn_analysis:
        turn_count = turn_analysis.get("total_turns", 0)
        responsive_turns = turn_analysis.get("responsive_turns", 0)

        if turn_count > 0:
            response_rate = responsive_turns / turn_count
            if response_rate > 0.7:
                score += 1.5
            elif response_rate > 0.5:
                score += 0.5
            elif response_rate < 0.3:
                score -= 1

    return max(0, min(4, score))


def score_belief_continuity(text: str, audits: Dict) -> int:
    """Grade belief continuity (0-4).

    Checks for:
    - Ritual concessions without state change (bad)
    - Explicit updates and remembering (good)
    - Contradiction resets (bad)
    """
    tags = audits.get("failure_tags", [])
    score = 2  # Start at neutral

    if "RITUAL_CONCESSION" in tags:
        score -= 1
    if "BELIEF_RESET" in tags:
        score -= 1

    # Automated check: do concessions appear in later turns?
    if "concession_carried_forward" in audits:
        score += 1

    return max(0, min(4, score))


def score_agency_asymmetry(audits: Dict) -> int:
    """Grade agency/asymmetry (0-4).

    Checks for:
    - Round-robin scheduling (bad)
    - Ornamental hosts (bad)
    - Organic participation (good)
    """
    tags = audits.get("failure_tags", [])
    score = 2  # Start at neutral

    if "ROUND_ROBIN" in tags:
        score -= 1
    if "ORNAMENTAL_HOST" in tags:
        score -= 0.5
    if "UNEQUAL_AIRTIME" in tags:
        score -= 0.25

    return max(0, min(4, score))


def score_anti_caricature(text: str, audits: Dict, character_domains: Dict = None) -> int:
    """Grade anti-caricature coherence (0-4).

    Checks for:
    - Caricature activation (bad)
    - Cross-domain application (good)
    - Value consistency (good)
    """
    tags = audits.get("failure_tags", [])
    score = 2  # Start at neutral

    if "CARICATURE_ACTIVATION" in tags:
        score -= 1
    if "REPETITIVE_ROLE" in tags:
        score -= 0.5

    if character_domains:
        if character_domains.get("values_apply_across_domains"):
            score += 1
        if character_domains.get("characters_cross_domains"):
            score += 0.5

    return max(0, min(4, score))


def score_naturalism(text: str, audits: Dict) -> int:
    """Grade naturalism (0-4).

    Checks for:
    - Template artifacts (bad)
    - Fallback mode markers (bad)
    - Natural variation (good)
    """
    tags = audits.get("failure_tags", [])
    score = 2  # Start at neutral

    if "FALLBACK_TEMPLATE" in tags:
        score -= 1
    if audits.get("template_artifacts"):
        score -= 0.5

    # Automated: check for vocabulary variety
    if audits.get("low_vocabulary_variety"):
        score -= 0.5
    if audits.get("natural_variation"):
        score += 1

    return max(0, min(4, score))


def aggregate_episode_score(annotations: List[Dict]) -> Dict[str, float]:
    """Aggregate multiple reviewer scores into median per dimension.

    Args:
        annotations: List of annotation dicts with scores

    Returns:
        Dict with median score per dimension
    """
    dimensions = [
        "evidence_score",
        "character_score",
        "conversation_score",
        "belief_score",
        "agency_score",
        "anti_caricature_score",
        "naturalism_score",
    ]

    aggregated = {}
    for dim in dimensions:
        scores = sorted([a[dim] for a in annotations if a.get(dim) is not None])
        if scores:
            mid = len(scores) // 2
            if len(scores) % 2 == 1:
                aggregated[dim] = scores[mid]
            else:
                aggregated[dim] = (scores[mid - 1] + scores[mid]) / 2
        else:
            aggregated[dim] = 0

    return aggregated


def check_release_gates(scores: Dict[str, float]) -> Dict[str, bool]:
    """Check whether an episode passes release gates.

    Gates:
    - Character-contingent appraisal >= 3 (median)
    - Belief continuity >= 3 (median)
    - No unsupported central claims
    - No sentinel regression
    """
    return {
        "character_contingency_passes": scores.get("character_score", 0) >= 3,
        "belief_continuity_passes": scores.get("belief_score", 0) >= 3,
        "no_sentinel_regression": True,  # Would check against baseline
        "preferred_over_control": None,  # Set by counterfactual experiment
    }
