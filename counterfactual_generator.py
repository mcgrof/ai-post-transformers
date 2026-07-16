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
                                 drives_enabled: bool = False, config: Dict = None) -> Dict:
    """Generate episode transcript (control or treatment).

    Args:
        paper_id: Paper ID from benchmark set
        arxiv_url: arXiv URL to generate from
        drives_enabled: If True, inject SOUL drive instructions
        config: Generation config (uses default if None)

    Returns:
        Dict with transcript, metadata, and generation params
    """
    # Wire into actual generation pipeline
    from phase3_counterfactual_harness import generate_episode_with_drives as harness_generate

    if config is None:
        # Load default config
        config = {
            "podcast": {
                "llm_model": "claude-opus-4-8",
                "max_words": 3000,
            }
        }

    return harness_generate(arxiv_url, config, drives_enabled, paper_id)


def generate_pair(paper_id: str, arxiv_url: str, config: Dict = None) -> Dict:
    """Generate control and treatment versions of a paper.

    Args:
        paper_id: Paper ID
        arxiv_url: arXiv URL
        config: Generation config

    Returns:
        Dict with control and treatment transcripts
    """
    from phase3_counterfactual_harness import generate_counterfactual_pair

    return generate_counterfactual_pair(paper_id, arxiv_url, config or {})


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
    from phase3_counterfactual_harness import compare_pair

    return compare_pair(pair, backend_config)


def cmd_generate_pair(paper_id: str, arxiv_url: str):
    """Command: Generate one control/treatment pair."""
    pair = generate_pair(paper_id, arxiv_url)

    # Save results
    with open(COUNTERFACTUAL_RESULTS_PATH, 'a') as f:
        f.write(yaml.dump({"paper_id": paper_id, "pair": pair}))

    print(f"✓ Saved pair to {COUNTERFACTUAL_RESULTS_PATH}")


def cmd_generate_batch(count: int = 3):
    """Command: Generate batch of pairs from benchmark set.

    Args:
        count: Number of pairs to generate (default 3 for testing)
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

    print(f"Generating {len(papers)} control/treatment pairs from frozen benchmark set...\n")

    results = {
        "batch_start": datetime.now().isoformat(),
        "batch_size": len(papers),
        "pairs": [],
    }

    # Load generation config
    import config as config_module
    try:
        gen_config = config_module.load_config().get("podcast", {})
    except:
        gen_config = {"llm_model": "claude-opus-4-8"}

    for i, paper in enumerate(papers, 1):
        arxiv_id = paper.get("arxiv_id", "")
        title = paper.get("title", "")[:40]

        if not arxiv_id:
            print(f"  [{i:2d}] SKIP (no arxiv_id): {title}")
            continue

        arxiv_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        print(f"  [{i:2d}] {arxiv_id}: {title}")

        pair = generate_pair(arxiv_id, arxiv_url, gen_config)
        results["pairs"].append(pair)

    results["batch_end"] = datetime.now().isoformat()

    # Save results
    with open(COUNTERFACTUAL_RESULTS_PATH, 'w') as f:
        yaml.dump(results, f, default_flow_style=False)

    print(f"\n✓ Generated {len(results['pairs'])} pairs")
    print(f"✓ Saved to {COUNTERFACTUAL_RESULTS_PATH}")
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
        print(f"Results file not found. Run: generate-batch")
        sys.exit(1)

    with open(COUNTERFACTUAL_RESULTS_PATH) as f:
        data = yaml.safe_load(f)

    pairs = data.get("pairs", [])
    results = data.get("comparison_results", [])

    print("\n" + "=" * 70)
    print("PHASE 3: COUNTERFACTUAL TEST RESULTS")
    print("=" * 70)

    print(f"\nGenerated: {len(pairs)} control/treatment pairs")
    print(f"Generation date: {data.get('batch_start', '')}\n")

    if not results:
        print("No comparison results yet. Run: compare-batch")
        print("=" * 70)
        return

    print(f"Comparison Results:\n")

    deltas = []
    evidence_deltas = []
    character_deltas = []
    passes = 0

    for i, result in enumerate(results, 1):
        paper = result.get("paper_id", "")
        status = result.get("status", "ERROR")

        if status != "ERROR":
            control = result.get("control_avg", 0)
            treatment = result.get("treatment_avg", 0)
            delta = result.get("delta", 0)
            decision = result.get("decision", "FAIL")

            bar = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            print(f"  {paper:20s} {control:.1f} → {treatment:.1f} {bar} {delta:+.2f} [{decision}]")

            deltas.append(delta)
            evidence_deltas.append(result.get("evidence_delta", 0))
            character_deltas.append(result.get("character_delta", 0))

            if decision == "PASS":
                passes += 1
        else:
            print(f"  {paper:20s} ERROR: {result.get('error', 'Unknown')}")

    print()

    if deltas:
        avg_delta = sum(deltas) / len(deltas)
        avg_evidence = sum(evidence_deltas) / len(evidence_deltas) if evidence_deltas else 0
        avg_character = sum(character_deltas) / len(character_deltas) if character_deltas else 0

        print(f"Summary Statistics:")
        print(f"  Average aggregate delta:      {avg_delta:+.2f}")
        print(f"  Average evidence delta:       {avg_evidence:+.2f}")
        print(f"  Average character delta:      {avg_character:+.2f}")
        print(f"  Pairs with improvement:       {passes}/{len(deltas)}")

        print(f"\nDecision:")
        if avg_delta > 0.5 and avg_evidence > 0.3 and avg_character > 0.3:
            print(f"✓ PASS: Drives significantly improve authenticity")
            print(f"  → Promote SOUL drives to production")
        elif avg_delta > 0:
            print(f"⚠ MARGINAL: Drives help slightly")
            print(f"  → Iterate prompts and retest")
        else:
            print(f"✗ FAIL: Drives don't improve authenticity")
            print(f"  → Diagnose: evidence graphs? prompts? calibration?")

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
