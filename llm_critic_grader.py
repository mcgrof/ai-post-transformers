"""LLM-based critic for grading episode authenticity.

The critic reads episode transcripts (blinded from pipeline version
and intended drives) and scores across 7 dimensions using the rubric.
"""

import json
from typing import Dict, List, Tuple
from llm_backend import get_llm_backend, llm_call


# Prompt template for LLM critic grading
CRITIC_PROMPT_TEMPLATE = """
You are an expert podcast critic evaluating the authenticity of AI research
podcast episode transcripts. Your job is to score the episode across 7
dimensions that measure whether it sounds like genuine character thinking
(authentic) vs. scripted NPC behavior (artificial).

You will NOT see: pipeline version, intended drives, prompt template, or
any metadata. Just the transcript and the dimension you're evaluating.

TRANSCRIPT:
---
{transcript}
---

DIMENSION: {dimension}

DEFINITION:
{definition}

ANCHORS (reference examples):
Score 0 (Worst): {score_0_anchor}
Score 2 (Mixed): {score_2_anchor}
Score 4 (Best): {score_4_anchor}

TASK: Read the transcript carefully. Score this episode on the DIMENSION
using the 0-4 scale. Justify your score with specific evidence from the
transcript (or lack thereof).

RESPONSE FORMAT (JSON):
{{
  "dimension": "{dimension}",
  "score": <0-4>,
  "confidence": <0.0-1.0>,
  "evidence": "<specific quotes or observations justifying the score>",
  "failure_tags": [<list of relevant failure tags if score < 2>]
}}

Be honest. If the transcript shows strong evidence of the dimension, score
high. If it shows weakness, score low. Don't average or hedge — commit to
your assessment.
"""


def get_critic_prompt(dimension: str, definition: str, anchors: Dict[str, str], transcript: str) -> str:
    """Build critic prompt for a single dimension."""
    return CRITIC_PROMPT_TEMPLATE.format(
        transcript=transcript[:4000],  # Limit to avoid token explosion
        dimension=dimension,
        definition=definition,
        score_0_anchor=anchors.get("0", "Unknown"),
        score_2_anchor=anchors.get("2", "Unknown"),
        score_4_anchor=anchors.get("4", "Unknown"),
    )


def grade_dimension(transcript: str, dimension_name: str, definition: str,
                    anchors: Dict[str, str], backend_config: Dict = None) -> Dict:
    """Grade a single dimension using LLM critic.

    Args:
        transcript: Episode transcript text
        dimension_name: Name of dimension (e.g., "Evidence Contingency")
        definition: Full definition and scoring criteria
        anchors: Dict with "0", "2", "4" anchor descriptions
        backend_config: LLM backend config (uses default if None)

    Returns:
        Dict with score, confidence, evidence, failure_tags
    """
    if backend_config is None:
        backend_config = {
            "llm_backend": "anthropic",
            "llm_model": "claude-opus-4-8",
        }

    prompt = get_critic_prompt(dimension_name, definition, anchors, transcript)

    try:
        backend = get_llm_backend(backend_config)
        response = llm_call(
            backend,
            backend_config["llm_model"],
            prompt,
            temperature=0.3,  # Lower temp for consistency
            max_tokens=500,
        )

        # Parse JSON response
        if isinstance(response, str):
            # Extract JSON from response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                result = json.loads(json_str)
            else:
                result = {"error": "Could not parse JSON from response"}
        elif isinstance(response, dict):
            result = response
        else:
            result = {"error": "Unexpected response type"}

        return result

    except Exception as e:
        return {
            "dimension": dimension_name,
            "error": str(e),
            "score": 2,  # Default to neutral
            "confidence": 0.0,
        }


def grade_episode_full(transcript: str, backend_config: Dict = None) -> Dict[str, any]:
    """Grade an episode across all 7 dimensions using LLM critic.

    Args:
        transcript: Full episode transcript
        backend_config: LLM backend config

    Returns:
        Dict with scores, confidences, evidence, aggregate metrics
    """
    from MEASUREMENT_FRAMEWORK import DIMENSION_ANCHORS  # Would need to restructure

    # For now, use inline definitions
    dimensions = [
        {
            "name": "Evidence Contingency",
            "definition": "Do hosts say different things about different papers? Is the dialogue tied to specific evidence, assumptions, or omissions from this particular paper?",
            "anchors": {
                "0": "Generic or fabricated statements; could work for any paper; host reaction independent of content",
                "2": "Paper-specific summary but hosts don't interpret or react; could work for similar papers",
                "4": "Claims and reactions tied to specific evidence, assumptions, novel claims; different paper would produce substantively different discussion",
            }
        },
        {
            "name": "Character-Contingent Appraisal",
            "definition": "Do hosts appraise evidence according to distinct cognitive policies? Are they interchangeable or do they have different standards?",
            "anchors": {
                "0": "Hosts interchangeable or distinguished only by catchphrases like 'As a pragmatist...'; same reasoning pattern",
                "2": "Different vocabulary and emphasis; same underlying reasoning; predictable role",
                "4": "Different evidence selection, value criteria, doubt patterns; disagreement stems from distinct epistemic policies, not personality",
            }
        },
        {
            "name": "Conversational Causality",
            "definition": "Do turns affect each other meaningfully? Does each response actually depend on what was just said?",
            "anchors": {
                "0": "Turns can be shuffled without breaking logic; adjacent monologues with no response",
                "2": "Some replies and reactive moments; many turns could reorder without damage",
                "4": "Each important turn changes live question, another host's position, or direction; clear causal chain forward",
            }
        },
        {
            "name": "Belief Continuity",
            "definition": "Do hosts remember prior exchanges and update their beliefs explicitly?",
            "anchors": {
                "0": "Positions reset; objections repeat after being answered; no memory between turns",
                "2": "Positions consistent but static; hosts agree with points but don't integrate into reasoning",
                "4": "Concessions persist; confidence changes visible; unresolved objections referenced later; learning evident",
            }
        },
        {
            "name": "Agency/Asymmetry",
            "definition": "Does participation emerge from relevance to the discussion or from scheduling?",
            "anchors": {
                "0": "Equal airtime, mandatory contributions, round-robin scheduling, forced disagreement",
                "2": "Some organic ownership; some scheduled participation and filler",
                "4": "Silence when not needed, deference to expert, topic ownership from relevance, disagreement emerges organically",
            }
        },
        {
            "name": "Anti-Caricature Coherence",
            "definition": "Do hosts preserve their values across different domains or are they deployed stereotypically?",
            "anchors": {
                "0": "Hal always talks engineering, Ada always talks math, VERA always narrates (on command)",
                "2": "Recognizable characters but stereotyped; same patterns regardless of paper type",
                "4": "Characters cross domains naturally; values apply to different content; distinct blind spots preserved",
            }
        },
        {
            "name": "Naturalism",
            "definition": "Does it sound like thinking aloud or like reading a script?",
            "anchors": {
                "0": "Obviously templated, repetitive phrasing, unnatural constructions, filler exposition",
                "2": "Mostly listenable; some turns natural, others obviously constructed; generated artifacts visible",
                "4": "Varied, economical, socially plausible pacing; sounds like genuine conversation",
            }
        },
    ]

    results = {
        "transcript_length": len(transcript),
        "dimension_scores": {},
        "aggregate_score": 0,
        "confidence_avg": 0,
    }

    all_scores = []
    all_confidences = []

    print(f"Grading episode across 7 dimensions...\n")

    for dim in dimensions:
        print(f"  Grading {dim['name']}...", end=" ", flush=True)

        result = grade_dimension(
            transcript,
            dim["name"],
            dim["definition"],
            dim["anchors"],
            backend_config,
        )

        score = result.get("score", 2)
        confidence = result.get("confidence", 0.5)

        results["dimension_scores"][dim["name"]] = {
            "score": score,
            "confidence": confidence,
            "evidence": result.get("evidence", ""),
            "failure_tags": result.get("failure_tags", []),
            "error": result.get("error"),
        }

        all_scores.append(score)
        all_confidences.append(confidence)

        print(f"✓ {score:.1f} (confidence: {confidence:.2f})")

    # Aggregate
    results["aggregate_score"] = sum(all_scores) / len(all_scores) if all_scores else 0
    results["confidence_avg"] = sum(all_confidences) / len(all_confidences) if all_confidences else 0

    return results


def grade_episode_blinded(episode_id: int, transcript: str,
                         backend_config: Dict = None) -> Dict[str, any]:
    """Grade an episode while blinded to metadata.

    The grader doesn't know:
    - Episode ID or title
    - When it was published
    - Pipeline version
    - Intended drives
    - Prior grades

    Args:
        episode_id: For tracking (not revealed to grader)
        transcript: Episode transcript
        backend_config: LLM backend

    Returns:
        Grades and analysis
    """
    return grade_episode_full(transcript, backend_config)
