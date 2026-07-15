#!/usr/bin/env python3
"""Counterfactual test rig for measuring SOUL character drive effectiveness.

Generates same paper twice: once with drives (treatment) and once without (control).
Then compares LLM critic scores blindly to measure causal impact.

Usage:
    python counterfactual_generator.py generate-pair <paper_id> <arxiv_url> [--both]
    python counterfactual_generator.py generate-batch <count>
    python counterfactual_generator.py compare-batch
    python counterfactual_generator.py report
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List

from soul_loader import get_soul_profile
from llm_critic_grader import grade_episode_full
from frozen_benchmark_set import load_benchmark_set
import yaml


COUNTERFACTUAL_RESULTS_PATH = Path(__file__).parent / "counterfactual_results.yaml"


def build_drive_prompt_segment(character: str) -> str:
    """Build SOUL drive instructions for a character.

    These get injected into the generation prompt to steer the host
    toward their character-specific reasoning patterns.
    """
    profile = get_soul_profile(character)
    lens_policy = profile.get("lens_policy", {})
    conversation_policy = profile.get("conversation_policy", {})

    engagement_drives = lens_policy.get("engagement_drives", [])
    common_questions = lens_policy.get("common_questions", [])
    when_challenge = conversation_policy.get("when_to_challenge", [])

    prompt = f"""
{character}'s reasoning priorities:

Engagement drives:
{chr(10).join('- ' + d for d in engagement_drives)}

Questions {character} typically asks:
{chr(10).join('- ' + q for q in common_questions)}

When {character} challenges other hosts:
{chr(10).join('- ' + c for c in when_challenge[:2])}

Channel your response through {character}'s priorities.
Don't announce them; just think the way {character} thinks.
"""
    return prompt


def generate_episode_with_drives(paper_id: str, arxiv_url: str,
                                 drives_enabled: bool = False) -> Dict:
    """Generate episode transcript (control or treatment).

    Args:
        paper_id: Paper ID from benchmark set
        arxiv_url: arXiv URL to generate from
        drives_enabled: If True, inject SOUL drive instructions

    Returns:
        Dict with transcript, metadata, and generation params
    """
    # TODO: Wire into actual gen-podcast.py generation
    # For now, return a placeholder structure

    timestamp = datetime.now().isoformat()
    mode = "treatment_with_drives" if drives_enabled else "control_baseline"

    generation_params = {
        "paper_id": paper_id,
        "arxiv_url": arxiv_url,
        "drives_enabled": drives_enabled,
        "generated_at": timestamp,
    }

    if drives_enabled:
        generation_params["drive_prompts"] = {
            "hal": build_drive_prompt_segment("Hal"),
            "ada": build_drive_prompt_segment("Ada"),
            "vera": build_drive_prompt_segment("VERA"),
        }

    return {
        "mode": mode,
        "generation_params": generation_params,
        "transcript": "[PLACEHOLDER: Full episode transcript]",
        "status": "NOT_YET_IMPLEMENTED",
    }


def generate_pair(paper_id: str, arxiv_url: str) -> Dict:
    """Generate control and treatment versions of a paper.

    Args:
        paper_id: Paper ID
        arxiv_url: arXiv URL

    Returns:
        Dict with control and treatment transcripts
    """
    print(f"\nGenerating pair for {paper_id}...")

    control = generate_episode_with_drives(paper_id, arxiv_url, drives_enabled=False)
    print(f"  Control: generated")

    treatment = generate_episode_with_drives(paper_id, arxiv_url, drives_enabled=True)
    print(f"  Treatment: generated with drives")

    return {
        "paper_id": paper_id,
        "arxiv_url": arxiv_url,
        "generated_at": datetime.now().isoformat(),
        "control": control,
        "treatment": treatment,
    }


def grade_pair(paper_id: str, pair: Dict,
               backend_config: Dict = None) -> Dict:
    """Grade both versions of a pair using LLM critic (blinded).

    Args:
        paper_id: Paper ID (for tracking, not revealed to critic)
        pair: Dict with control and treatment transcripts
        backend_config: LLM backend config

    Returns:
        Dict with scores for both versions
    """
    print(f"  Grading pair (blinded)...")

    control_tx = pair["control"].get("transcript", "")
    treatment_tx = pair["treatment"].get("transcript", "")

    if not control_tx or "[PLACEHOLDER" in control_tx:
        print(f"    Skipping: placeholders in transcripts")
        return {
            "paper_id": paper_id,
            "control_score": None,
            "treatment_score": None,
            "status": "PLACEHOLDER",
        }

    # Grade control (blinded)
    control_grades = grade_episode_full(control_tx, backend_config)
    print(f"    Control: {control_grades['aggregate_score']:.1f}")

    # Grade treatment (blinded)
    treatment_grades = grade_episode_full(treatment_tx, backend_config)
    print(f"    Treatment: {treatment_grades['aggregate_score']:.1f}")

    return {
        "paper_id": paper_id,
        "control": control_grades,
        "treatment": treatment_grades,
        "delta": treatment_grades['aggregate_score'] - control_grades['aggregate_score'],
    }


def cmd_generate_pair(paper_id: str, arxiv_url: str):
    """Command: Generate one control/treatment pair."""
    pair = generate_pair(paper_id, arxiv_url)

    # Save results
    with open(COUNTERFACTUAL_RESULTS_PATH, 'a') as f:
        f.write(yaml.dump({"paper_id": paper_id, "pair": pair}))

    print(f"✓ Saved pair to {COUNTERFACTUAL_RESULTS_PATH}")


def cmd_generate_batch(count: int = 5):
    """Command: Generate batch of pairs from benchmark set.

    Args:
        count: Number of pairs to generate
    """
    # Load benchmark papers
    benchmark_path = Path(__file__).parent / "frozen_benchmark_set.yaml"
    if not benchmark_path.exists():
        print(f"Benchmark set not found at {benchmark_path}")
        sys.exit(1)

    with open(benchmark_path) as f:
        data = yaml.safe_load(f)

    papers = data.get("benchmark_papers", [])[:count]

    if not papers:
        print(f"No papers found in benchmark set")
        sys.exit(1)

    print(f"Generating {len(papers)} pairs from frozen benchmark set...\n")

    results = {
        "batch_start": datetime.now().isoformat(),
        "batch_size": len(papers),
        "pairs": [],
    }

    for i, paper in enumerate(papers, 1):
        arxiv_id = paper.get("arxiv_id", "")
        title = paper.get("title", "")[:40]

        if not arxiv_id:
            print(f"  [{i:2d}] SKIP (no arxiv_id): {title}")
            continue

        arxiv_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        print(f"  [{i:2d}] {arxiv_id}: {title}")

        pair = generate_pair(arxiv_id, arxiv_url)
        results["pairs"].append(pair)

    results["batch_end"] = datetime.now().isoformat()

    # Save results
    with open(COUNTERFACTUAL_RESULTS_PATH, 'w') as f:
        yaml.dump(results, f, default_flow_style=False)

    print(f"\n✓ Saved {len(results['pairs'])} pairs to {COUNTERFACTUAL_RESULTS_PATH}")
    print(f"\nNext: python counterfactual_generator.py compare-batch")


def cmd_compare_batch():
    """Command: Grade all pairs in batch."""
    if not COUNTERFACTUAL_RESULTS_PATH.exists():
        print(f"Results file not found. Run: generate-batch")
        sys.exit(1)

    with open(COUNTERFACTUAL_RESULTS_PATH) as f:
        data = yaml.safe_load(f)

    pairs = data.get("pairs", [])

    if not pairs:
        print(f"No pairs to grade")
        sys.exit(1)

    print(f"Grading {len(pairs)} pairs...\n")

    all_results = []
    for i, pair in enumerate(pairs, 1):
        paper_id = pair.get("paper_id", "")
        print(f"  [{i:2d}] {paper_id}")

        # Note: Will be placeholder until generation is wired up
        result = grade_pair(paper_id, pair)
        all_results.append(result)

    # Save graded results
    data["comparison_results"] = all_results
    data["comparison_at"] = datetime.now().isoformat()

    with open(COUNTERFACTUAL_RESULTS_PATH, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)

    print(f"\n✓ Saved comparison results to {COUNTERFACTUAL_RESULTS_PATH}")
    print(f"\nNext: python counterfactual_generator.py report")


def cmd_report():
    """Command: Generate comparison report."""
    if not COUNTERFACTUAL_RESULTS_PATH.exists():
        print(f"Results file not found")
        sys.exit(1)

    with open(COUNTERFACTUAL_RESULTS_PATH) as f:
        data = yaml.safe_load(f)

    results = data.get("comparison_results", [])

    if not results or all(r.get("status") == "PLACEHOLDER" for r in results):
        print("=" * 70)
        print("COUNTERFACTUAL TEST RIG — CONFIGURATION")
        print("=" * 70)
        print(f"\nStatus: Awaiting real episode generation\n")
        print("Framework ready:")
        print("  • Evidence graph extraction: Designed")
        print("  • Drive-activated prompts: Built (build_drive_prompt_segment)")
        print("  • Paired generation harness: Built (generate_pair)")
        print("  • Blind comparison: Built (grade_pair)")
        print("  • Statistical analysis: Ready\n")
        print("Next: Wire into gen-podcast.py to generate real episodes\n")
        print("=" * 70)
        return

    # Real results
    print("=" * 70)
    print("COUNTERFACTUAL TEST RIG — RESULTS")
    print("=" * 70)

    print(f"\nBatch size: {len(results)} pairs")
    print(f"Comparison date: {data.get('comparison_at', '')}\n")

    print("Results (Treatment vs Control):\n")

    deltas = []
    for result in results:
        paper = result.get("paper_id", "")
        delta = result.get("delta", 0)
        status = result.get("status", "SCORED")

        if status == "PLACEHOLDER":
            print(f"  {paper:20s} [PLACEHOLDER]")
        else:
            bar = "→" if delta >= 0 else "←"
            print(f"  {paper:20s} {bar:1s} {delta:+.2f}")
            deltas.append(delta)

    if deltas:
        avg_delta = sum(deltas) / len(deltas)
        print(f"\n  Average delta: {avg_delta:+.2f}")
        print(f"  Median delta:  {sorted(deltas)[len(deltas)//2]:+.2f}")

        if avg_delta > 0.5:
            print(f"\n✓ PASS: Drives improve authenticity (δ > 0.5)")
        elif avg_delta > 0:
            print(f"\n⚠ MARGINAL: Drives help slightly (δ = {avg_delta:.2f})")
        else:
            print(f"\n✗ FAIL: Drives don't improve (δ ≤ 0)")

        print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Counterfactual test rig")
    subparsers = parser.add_subparsers(dest="command")

    gen_cmd = subparsers.add_parser("generate-pair", help="Generate one pair")
    gen_cmd.add_argument("paper_id", help="Paper ID (arxiv ID)")
    gen_cmd.add_argument("arxiv_url", help="arXiv PDF URL")

    batch_cmd = subparsers.add_parser("generate-batch", help="Generate batch")
    batch_cmd.add_argument("--count", type=int, default=5, help="Number of pairs")

    subparsers.add_parser("compare-batch", help="Grade all pairs")
    subparsers.add_parser("report", help="Display report")

    args = parser.parse_args()

    if args.command == "generate-pair":
        cmd_generate_pair(args.paper_id, args.arxiv_url)
    elif args.command == "generate-batch":
        cmd_generate_batch(args.count)
    elif args.command == "compare-batch":
        cmd_compare_batch()
    elif args.command == "report":
        cmd_report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
