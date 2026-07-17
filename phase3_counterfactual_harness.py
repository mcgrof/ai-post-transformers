#!/usr/bin/env python3
"""Phase 3: Counterfactual test harness.

Integrates with actual generation pipeline to run control/treatment
experiments. Injects SOUL drives into prompts when treatment=True.
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

from soul_loader import get_soul_profile
from elevenlabs_client import generate_podcast_script
from db import get_connection, init_db, insert_podcast, list_podcasts
from pdf_utils import download_and_extract
import json


def build_soul_drive_segment(character: str, dimension: str = "evidence") -> str:
    """Build SOUL drive instructions for a character.

    Args:
        character: "Hal", "Ada", or "VERA"
        dimension: Focus dimension ("evidence", "character", "belief")

    Returns:
        Formatted instruction segment for LLM prompt
    """
    profile = get_soul_profile(character)
    lens = profile.get("lens_policy", {})
    conversation = profile.get("conversation_policy", {})

    if dimension == "evidence":
        drives = lens.get("engagement_drives", [])[:2]
        questions = lens.get("common_questions", [])[:2]
    elif dimension == "character":
        questions = conversation.get("when_to_challenge", [])[:2]
        drives = lens.get("engagement_drives", [])
    else:
        drives = lens.get("engagement_drives", [])
        questions = conversation.get("how_to_update_beliefs", [])[:2]

    prompt = f"""
{character}'s reasoning mode (activated):

Thinking priorities:
{chr(10).join('- ' + d for d in drives[:3]) if drives else "- Ground reasoning in specific evidence from the paper"}

Questions {character} typically asks:
{chr(10).join('- ' + q for q in questions[:2]) if questions else "- What's the evidence for this claim?"}

Embody {character}'s perspective: don't announce it, just think this way.
Let {character}'s priorities guide which evidence you select and how you interpret it.
"""
    return prompt.strip()


def extract_paper_content(url: str) -> str:
    """Extract text from paper URL.

    Args:
        url: arXiv URL

    Returns:
        Extracted text or metadata
    """
    try:
        text = download_and_extract(url)
        if len(text) > 3000:
            # Return first 3000 chars of abstract/intro
            return text[:3000]
        return text
    except Exception as e:
        # Fallback to metadata extraction
        if "arxiv" in url.lower():
            arxiv_id = url.split("/")[-1].replace(".pdf", "")
            return f"Paper ID: {arxiv_id}\nUnable to extract full text; using metadata."
        return f"Error extracting paper: {e}"


def generate_episode_with_drives(
    arxiv_url: str,
    config: Dict,
    drives_enabled: bool = False,
    paper_id: str = None
) -> Dict:
    """Generate episode transcript with optional SOUL drives.

    Args:
        arxiv_url: arXiv PDF URL
        config: Generation config
        drives_enabled: If True, inject SOUL drive prompts
        paper_id: Paper identifier for tracking

    Returns:
        Dict with transcript, metadata, generation params
    """
    print(f"\n{'[Treatment]' if drives_enabled else '[Control]'} "
          f"Generating from {arxiv_url[:50]}...", file=sys.stderr)

    # Extract paper content
    paper_text = extract_paper_content(arxiv_url)

    # Prepare generation config with proper LLM backend
    generation_config = {
        "podcast": {
            "llm_backend": "claude-cli",
            "llm_model": "opus",
            "max_words": 3000,
        }
    }

    if drives_enabled:
        # Add SOUL drive context to config
        generation_config["podcast"]["soul_drives"] = {
            "Hal": build_soul_drive_segment("Hal", "evidence"),
            "Ada": build_soul_drive_segment("Ada", "evidence"),
            "VERA": build_soul_drive_segment("VERA", "evidence"),
        }
        generation_config["podcast"]["drives_enabled"] = True

    # Generate script
    try:
        script_result = generate_podcast_script(
            paper_text,
            generation_config,
            covered_topics=set(),
            opening_reason=None,
            primary_host="Hal" if drives_enabled else "Ada"  # Vary opening host
        )

        # Unpack result (script, sources, topics, reason_used)
        if isinstance(script_result, tuple):
            script, sources, topics, reason_used = script_result[:4]
        else:
            script = script_result
            sources = []
            topics = []
            reason_used = None

        # Convert script to transcript text
        transcript = "\n".join([f"{s['speaker']}: {s['text']}" for s in script])

        return {
            "mode": "treatment_with_drives" if drives_enabled else "control_baseline",
            "paper_id": paper_id,
            "arxiv_url": arxiv_url,
            "drives_enabled": drives_enabled,
            "transcript": transcript,
            "script": script,
            "sources": sources,
            "topics": topics,
            "status": "GENERATED",
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        print(f"Error generating episode: {e}", file=sys.stderr)
        return {
            "mode": "treatment_with_drives" if drives_enabled else "control_baseline",
            "paper_id": paper_id,
            "arxiv_url": arxiv_url,
            "drives_enabled": drives_enabled,
            "status": "ERROR",
            "error": str(e),
            "generated_at": datetime.now().isoformat(),
        }


def generate_counterfactual_pair(
    arxiv_id: str,
    arxiv_url: str,
    config: Dict
) -> Dict:
    """Generate control and treatment versions of same paper.

    Args:
        arxiv_id: Paper ID (for tracking)
        arxiv_url: arXiv PDF URL
        config: Generation config

    Returns:
        Dict with control and treatment transcripts
    """
    print(f"\n{'='*70}")
    print(f"COUNTERFACTUAL: {arxiv_id}")
    print(f"{'='*70}")

    # Control: baseline generation
    control = generate_episode_with_drives(
        arxiv_url, config,
        drives_enabled=False,
        paper_id=arxiv_id
    )

    # Treatment: with SOUL drives
    treatment = generate_episode_with_drives(
        arxiv_url, config,
        drives_enabled=True,
        paper_id=arxiv_id
    )

    return {
        "paper_id": arxiv_id,
        "arxiv_url": arxiv_url,
        "generated_at": datetime.now().isoformat(),
        "control": control,
        "treatment": treatment,
        "control_status": control.get("status"),
        "treatment_status": treatment.get("status"),
    }


def grade_transcript_blinded(
    transcript: str,
    paper_id: str,
    backend_config: Dict = None
) -> Dict:
    """Grade transcript using LLM critic (blinded to control/treatment).

    Args:
        transcript: Full episode transcript
        paper_id: For tracking (not revealed to grader)
        backend_config: LLM backend config

    Returns:
        Dict with 7 dimension scores
    """
    if not transcript or len(transcript) < 100:
        return {
            "paper_id": paper_id,
            "status": "ERROR",
            "error": "Transcript too short or empty",
        }

    # Import here to avoid circular dependency
    from llm_critic_grader import grade_episode_full

    try:
        scores = grade_episode_full(transcript, backend_config)
        return {
            "paper_id": paper_id,
            "status": "SCORED",
            **scores,
        }
    except Exception as e:
        return {
            "paper_id": paper_id,
            "status": "ERROR",
            "error": str(e),
        }


def compare_pair(pair: Dict, backend_config: Dict = None) -> Dict:
    """Grade both versions of a pair and compare.

    Args:
        pair: Dict with control and treatment
        backend_config: LLM backend config

    Returns:
        Dict with scores, delta, decision
    """
    paper_id = pair.get("paper_id", "")

    control_tx = pair.get("control", {}).get("transcript", "")
    treatment_tx = pair.get("treatment", {}).get("transcript", "")

    if not control_tx or not treatment_tx:
        return {
            "paper_id": paper_id,
            "status": "ERROR",
            "error": "Missing transcripts",
        }

    print(f"\n  Grading {paper_id} (blinded)...")

    # Grade control
    print(f"    Control...", end=" ", flush=True)
    control_scores = grade_transcript_blinded(control_tx, paper_id, backend_config)
    if control_scores.get("status") == "SCORED":
        control_avg = control_scores.get("aggregate_score", 0)
        print(f"✓ {control_avg:.1f}")
    else:
        print(f"✗ Error")
        control_avg = 0

    # Grade treatment
    print(f"    Treatment...", end=" ", flush=True)
    treatment_scores = grade_transcript_blinded(treatment_tx, paper_id, backend_config)
    if treatment_scores.get("status") == "SCORED":
        treatment_avg = treatment_scores.get("aggregate_score", 0)
        print(f"✓ {treatment_avg:.1f}")
    else:
        print(f"✗ Error")
        treatment_avg = 0

    delta = treatment_avg - control_avg

    # Check primary metrics (evidence contingency, character appraisal, belief continuity)
    evidence_delta = treatment_scores.get("dimension_scores", {}).get("Evidence Contingency", {}).get("score", 0) - \
                     control_scores.get("dimension_scores", {}).get("Evidence Contingency", {}).get("score", 0)

    character_delta = treatment_scores.get("dimension_scores", {}).get("Character-Contingent Appraisal", {}).get("score", 0) - \
                      control_scores.get("dimension_scores", {}).get("Character-Contingent Appraisal", {}).get("score", 0)

    return {
        "paper_id": paper_id,
        "control_avg": control_avg,
        "treatment_avg": treatment_avg,
        "delta": delta,
        "evidence_delta": evidence_delta,
        "character_delta": character_delta,
        "decision": "PASS" if delta > 0.5 else ("MARGINAL" if delta > 0 else "FAIL"),
        "control_scores": control_scores,
        "treatment_scores": treatment_scores,
    }
