#!/usr/bin/env python3
"""Phase 3: Lightweight counterfactual test harness.

Uses simple prompt-based generation (not full podcast pipeline) to test
whether SOUL drives improve authenticity scoring. Fast path for validation.
"""

import sys
import json
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict

from soul_loader import get_soul_profile
from llm_backend import get_llm_backend, llm_call
from pdf_utils import download_and_extract


def generate_simple_discussion(
    paper_text: str,
    paper_title: str,
    drives_enabled: bool = False,
) -> str:
    """Generate a simple discussion (no full podcast pipeline).

    Args:
        paper_text: Extracted paper text
        paper_title: Paper title
        drives_enabled: If True, inject SOUL drives

    Returns:
        Discussion transcript
    """
    if not paper_text or len(paper_text) < 100:
        return "ERROR: Paper text too short"

    # Truncate to first 2000 chars to avoid timeout
    paper_excerpt = paper_text[:2000]

    # Build prompt
    system_prompt = """You are two AI researchers (Hal and Ada) having a short discussion.
Hal is pragmatic and focuses on shipping/scale.
Ada is a mathematician who values rigor and generality.

Generate a natural back-and-forth dialogue about the paper.
Format:
Hal: <text>
Ada: <text>
(repeat 4-6 turns total)

Keep it concise and evidence-grounded."""

    if drives_enabled:
        # Add SOUL drives to system prompt
        hal_drives = get_soul_profile("Hal").get("lens_policy", {}).get("engagement_drives", [])[:2]
        ada_drives = get_soul_profile("Ada").get("lens_policy", {}).get("engagement_drives", [])[:2]

        system_prompt += f"""

CRITICAL: Follow these reasoning modes:
Hal's priorities: {'; '.join(hal_drives) if hal_drives else 'ground reasoning in evidence'}
Ada's priorities: {'; '.join(ada_drives) if ada_drives else 'demand rigor and proof'}

Let these priorities shape which evidence you select and how you interpret it."""

    user_prompt = f"""Paper: {paper_title}

Excerpt: {paper_excerpt}

Generate a 4-6 turn discussion between Hal and Ada about this paper."""

    # Call LLM
    config = {"podcast": {"llm_backend": "claude-cli", "llm_model": "opus"}}
    backend = get_llm_backend(config)

    try:
        response = llm_call(
            backend,
            "opus",
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=2000,
        )

        if isinstance(response, dict):
            return response.get("text", str(response))
        return str(response)
    except Exception as e:
        return f"ERROR: {e}"


def run_lightweight_counterfactual(
    arxiv_url: str,
    paper_title: str,
    arxiv_id: str,
) -> Dict:
    """Run lightweight control/treatment test.

    Args:
        arxiv_url: arXiv PDF URL
        paper_title: Paper title
        arxiv_id: Paper ID for tracking

    Returns:
        Dict with control/treatment transcripts and comparison
    """
    print(f"\n{'='*70}")
    print(f"LIGHTWEIGHT COUNTERFACTUAL: {arxiv_id}")
    print(f"{'='*70}")

    # Extract paper
    print(f"Extracting paper from {arxiv_url}...")
    try:
        paper_text = download_and_extract(arxiv_url)
        if len(paper_text) < 100:
            return {
                "paper_id": arxiv_id,
                "status": "ERROR",
                "error": "Paper extraction failed",
            }
    except Exception as e:
        return {
            "paper_id": arxiv_id,
            "status": "ERROR",
            "error": f"Download failed: {e}",
        }

    # Generate control
    print(f"Generating control (baseline)...")
    control_transcript = generate_simple_discussion(
        paper_text, paper_title, drives_enabled=False
    )

    # Generate treatment
    print(f"Generating treatment (with drives)...")
    treatment_transcript = generate_simple_discussion(
        paper_text, paper_title, drives_enabled=True
    )

    return {
        "paper_id": arxiv_id,
        "paper_title": paper_title,
        "arxiv_url": arxiv_url,
        "generated_at": datetime.now().isoformat(),
        "control": {
            "transcript": control_transcript,
            "mode": "control_baseline",
            "status": "GENERATED" if control_transcript and not control_transcript.startswith("ERROR") else "ERROR",
        },
        "treatment": {
            "transcript": treatment_transcript,
            "mode": "treatment_with_drives",
            "status": "GENERATED" if treatment_transcript and not treatment_transcript.startswith("ERROR") else "ERROR",
        },
    }


def main():
    # Test with 2 small papers
    papers = [
        {
            "id": "2405.14825",
            "title": "LongRoPE: Extending LLM Context Window Beyond 2M Tokens",
            "url": "https://arxiv.org/pdf/2405.14825.pdf",
        },
        {
            "id": "2405.13962",
            "title": "Ring Attention with Blockwise Transformers for Context Scalability",
            "url": "https://arxiv.org/pdf/2405.13962.pdf",
        },
    ]

    results = []
    for paper in papers:
        result = run_lightweight_counterfactual(
            paper["url"],
            paper["title"],
            paper["id"],
        )
        results.append(result)
        print(f"\nControl ({len(result['control']['transcript'])} chars):")
        print(result['control']['transcript'][:300] + "...")
        print(f"\nTreatment ({len(result['treatment']['transcript'])} chars):")
        print(result['treatment']['transcript'][:300] + "...")

    # Save results
    output_file = Path("counterfactual_results_lightweight.yaml")
    with open(output_file, "w") as f:
        yaml.dump({
            "test_type": "lightweight",
            "generated_at": datetime.now().isoformat(),
            "papers": results,
        }, f)

    print(f"\n✓ Saved results to {output_file}")
    return results


if __name__ == "__main__":
    results = main()
