#!/usr/bin/env python3
"""Phase 2: Retrospective Calibration

Sample historical published episodes, run audits, score dimensions,
identify anchor examples, and establish baseline variance.

Usage:
    python phase2_retrospective_calibration.py sample [--count 25]
    python phase2_retrospective_calibration.py audit-sample
    python phase2_retrospective_calibration.py analyze
    python phase2_retrospective_calibration.py report
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import statistics

from db import get_connection, list_podcasts
from episode_evaluation_db import (
    get_eval_connection, init_eval_db, create_episode_run,
    record_annotation, get_episode_annotations, aggregate_scores
)
from authenticity_audit import audit_episode
from grading_rubric import (
    score_evidence_contingency, score_character_contingency,
    score_conversational_causality, score_belief_continuity,
    score_agency_asymmetry, score_anti_caricature, score_naturalism,
    DIMENSION_ANCHORS
)
import yaml


CALIBRATION_RESULTS_PATH = Path(__file__).parent / "calibration_results.yaml"


def sample_episodes(count: int = 25) -> list:
    """Sample diverse historical published episodes for calibration.

    Args:
        count: Number of episodes to sample

    Returns:
        List of (episode_id, title, publish_date) tuples
    """
    conn = get_connection()
    episodes = list_podcasts(conn)
    conn.close()

    if not episodes:
        print("No episodes found in database")
        return []

    # Stratify by date (recent, mid, old)
    sorted_eps = sorted(episodes, key=lambda e: e.get("publish_date", ""))

    total = len(sorted_eps)
    sample = []

    # Take evenly spaced samples
    if total >= count:
        step = total // count
        for i in range(0, total, step):
            if len(sample) < count:
                ep = sorted_eps[i]
                sample.append({
                    "id": ep["id"],
                    "title": ep.get("title", ""),
                    "publish_date": ep.get("publish_date", ""),
                    "description": ep.get("description", ""),
                })
    else:
        sample = [{
            "id": ep["id"],
            "title": ep.get("title", ""),
            "publish_date": ep.get("publish_date", ""),
            "description": ep.get("description", ""),
        } for ep in sorted_eps]

    print(f"Sampled {len(sample)} episodes")
    for ep in sample:
        print(f"  {ep['id']:3d} | {ep['publish_date']:10s} | {ep['title'][:50]}")

    return sample


def audit_sample(episodes: list = None) -> dict:
    """Run full audit and grading on sampled episodes.

    Args:
        episodes: List of episode dicts; if None, use saved sample

    Returns:
        Dict mapping episode_id → audit results
    """
    if episodes is None:
        # Load from file if available
        if CALIBRATION_RESULTS_PATH.exists():
            with open(CALIBRATION_RESULTS_PATH) as f:
                data = yaml.safe_load(f) or {}
                episodes = data.get("sample", [])
        else:
            episodes = sample_episodes()

    results = {}
    eval_conn = get_eval_connection()
    init_eval_db(eval_conn)

    print(f"\nAuditing {len(episodes)} episodes...\n")

    for i, ep in enumerate(episodes, 1):
        ep_id = ep["id"]
        transcript = ep.get("description", "")

        if not transcript:
            print(f"  [{i:2d}] {ep_id:3d} SKIP (no transcript)")
            continue

        # Run audit
        lines = transcript.split('\n')
        audit_results = audit_episode(transcript, lines)

        # Score dimensions
        scores = {
            "evidence_score": score_evidence_contingency(transcript, audit_results),
            "character_score": score_character_contingency(transcript, audit_results),
            "conversation_score": score_conversational_causality(lines, {}),
            "belief_score": score_belief_continuity(transcript, audit_results),
            "agency_score": score_agency_asymmetry(audit_results),
            "anti_caricature_score": score_anti_caricature(transcript, audit_results),
            "naturalism_score": score_naturalism(transcript, audit_results),
        }

        # Record in database
        run_id = f"calibration_ep_{ep_id}"
        record_annotation(
            eval_conn,
            run_id=run_id,
            unit_type="episode",
            unit_id=str(ep_id),
            reviewer_id="phase2_automated",
            reviewer_type="automated",
            scores=scores,
            confidence=0.7,
            failure_tags=audit_results["failure_tags"],
            notes=f"Phase 2 calibration: {audit_results['severity_count']} audit failures"
        )

        # Compute aggregate score
        avg_score = sum(scores.values()) / len(scores)
        bar = "█" * int(avg_score) + "░" * (4 - int(avg_score))

        print(f"  [{i:2d}] {ep_id:3d} {bar} {avg_score:.1f} | "
              f"{len(audit_results['failure_tags'])} tags | "
              f"{ep['title'][:40]}")

        results[ep_id] = {
            "title": ep["title"],
            "publish_date": ep.get("publish_date", ""),
            "scores": scores,
            "audit_results": {
                "failure_tags": audit_results["failure_tags"],
                "severity_count": audit_results["severity_count"],
                "generic_openers": len(audit_results.get("generic_openers", [])),
                "persona_declarations": len(audit_results.get("persona_declarations", [])),
                "round_robin": "ROUND_ROBIN" in audit_results["failure_tags"],
            }
        }

    eval_conn.close()

    # Save results
    with open(CALIBRATION_RESULTS_PATH, 'w') as f:
        yaml.dump({
            "calibration_date": datetime.now().isoformat(),
            "sample_size": len(results),
            "sample": [{"id": ep["id"], "title": ep["title"], "publish_date": ep.get("publish_date")} for ep in episodes],
            "audit_results": results,
        }, f, default_flow_style=False, sort_keys=False)

    print(f"\n✓ Saved results to {CALIBRATION_RESULTS_PATH}")
    return results


def analyze_calibration() -> dict:
    """Analyze audit results to calibrate rubric anchors.

    Returns:
        Dict with statistics, anchor examples, baseline variance
    """
    if not CALIBRATION_RESULTS_PATH.exists():
        print("No calibration results found. Run: audit-sample")
        sys.exit(1)

    with open(CALIBRATION_RESULTS_PATH) as f:
        data = yaml.safe_load(f)

    results = data.get("audit_results", {})

    if not results:
        print("No audit results to analyze")
        sys.exit(1)

    # Collect scores per dimension
    dimension_scores = defaultdict(list)
    all_scores = []

    for ep_id, ep_data in results.items():
        scores = ep_data.get("scores", {})
        for dim, score in scores.items():
            dimension_scores[dim].append(score)
            all_scores.append(score)

    # Compute statistics
    analysis = {
        "calibration_date": data.get("calibration_date"),
        "sample_size": len(results),
        "dimension_statistics": {},
        "anchor_candidates": {},
        "failure_tag_frequency": defaultdict(int),
    }

    # Stats per dimension
    dimensions = [
        ("Evidence Contingency", "evidence_score"),
        ("Character-Contingent Appraisal", "character_score"),
        ("Conversational Causality", "conversation_score"),
        ("Belief Continuity", "belief_score"),
        ("Agency/Asymmetry", "agency_score"),
        ("Anti-Caricature Coherence", "anti_caricature_score"),
        ("Naturalism", "naturalism_score"),
    ]

    for dim_name, dim_key in dimensions:
        scores = sorted(dimension_scores.get(dim_key, []))

        if scores:
            stats = {
                "mean": statistics.mean(scores),
                "median": statistics.median(scores),
                "stdev": statistics.stdev(scores) if len(scores) > 1 else 0,
                "min": min(scores),
                "max": max(scores),
                "q25": scores[len(scores) // 4],
                "q75": scores[len(scores) - len(scores) // 4 - 1],
            }
            analysis["dimension_statistics"][dim_name] = stats

    # Find anchor candidates (0, 2, 4)
    for dim_name, dim_key in dimensions:
        scores = sorted(dimension_scores.get(dim_key, []))
        if not scores:
            continue

        # Episodes with lowest scores (score 0-1)
        low_eps = [
            (ep_id, ep_data["scores"][dim_key], ep_data["title"])
            for ep_id, ep_data in results.items()
            if ep_data["scores"].get(dim_key, 0) <= 1
        ]

        # Episodes with mid scores (score 2-2.5)
        mid_eps = [
            (ep_id, ep_data["scores"][dim_key], ep_data["title"])
            for ep_id, ep_data in results.items()
            if 1.5 <= ep_data["scores"].get(dim_key, 0) <= 2.5
        ]

        # Episodes with highest scores (score 3.5+)
        high_eps = [
            (ep_id, ep_data["scores"][dim_key], ep_data["title"])
            for ep_id, ep_data in results.items()
            if ep_data["scores"].get(dim_key, 0) >= 3.5
        ]

        analysis["anchor_candidates"][dim_name] = {
            "low": sorted(low_eps, key=lambda x: x[1])[:3],
            "mid": sorted(mid_eps, key=lambda x: abs(x[1] - 2))[:3],
            "high": sorted(high_eps, key=lambda x: x[1], reverse=True)[:3],
        }

    # Failure tag frequency
    for ep_id, ep_data in results.items():
        for tag in ep_data.get("audit_results", {}).get("failure_tags", []):
            analysis["failure_tag_frequency"][tag] += 1

    analysis["failure_tag_frequency"] = dict(
        sorted(analysis["failure_tag_frequency"].items(),
               key=lambda x: x[1], reverse=True)
    )

    return analysis


def generate_calibration_report(analysis: dict = None) -> str:
    """Generate human-readable calibration report.

    Args:
        analysis: Results from analyze_calibration()

    Returns:
        Formatted report string
    """
    if analysis is None:
        analysis = analyze_calibration()

    report = []
    report.append("=" * 70)
    report.append("PHASE 2: RETROSPECTIVE CALIBRATION REPORT")
    report.append("=" * 70)
    report.append(f"\nCalibration Date: {analysis['calibration_date']}")
    report.append(f"Sample Size: {analysis['sample_size']} episodes\n")

    # Dimension statistics
    report.append("\n" + "=" * 70)
    report.append("DIMENSION STATISTICS (Baseline Variance)")
    report.append("=" * 70)

    for dim_name, stats in analysis["dimension_statistics"].items():
        report.append(f"\n{dim_name}")
        report.append(f"  Mean:   {stats['mean']:.2f}")
        report.append(f"  Median: {stats['median']:.2f}")
        report.append(f"  StDev:  {stats['stdev']:.2f}")
        report.append(f"  Range:  {stats['min']:.1f} — {stats['max']:.1f}")
        report.append(f"  IQR:    {stats['q25']:.1f} — {stats['q75']:.1f}")

    # Anchor candidates
    report.append("\n" + "=" * 70)
    report.append("ANCHOR CANDIDATES (Examples for Rubric Calibration)")
    report.append("=" * 70)

    for dim_name, anchors in analysis["anchor_candidates"].items():
        report.append(f"\n{dim_name}")

        report.append("  Score 0 (Worst):")
        for ep_id, score, title in anchors["low"]:
            report.append(f"    [{ep_id:3d}] {score:.1f} | {title[:50]}")

        report.append("  Score 2 (Mixed):")
        for ep_id, score, title in anchors["mid"]:
            report.append(f"    [{ep_id:3d}] {score:.1f} | {title[:50]}")

        report.append("  Score 4 (Best):")
        for ep_id, score, title in anchors["high"]:
            report.append(f"    [{ep_id:3d}] {score:.1f} | {title[:50]}")

    # Failure tag frequency
    report.append("\n" + "=" * 70)
    report.append("FAILURE TAG FREQUENCY (NPC Pattern Prevalence)")
    report.append("=" * 70)

    tag_freq = analysis["failure_tag_frequency"]
    total_episodes = analysis["sample_size"]

    for tag, count in tag_freq.items():
        pct = 100 * count / total_episodes
        bar = "█" * int(pct / 5)
        report.append(f"  {tag:30s} {bar:20s} {count:2d} ({pct:5.1f}%)")

    # Insights
    report.append("\n" + "=" * 70)
    report.append("INSIGHTS & NEXT STEPS")
    report.append("=" * 70)

    most_common_tag = tag_freq.get(list(tag_freq.keys())[0], 0) if tag_freq else 0
    high_variance_dims = [
        (dim, stats["stdev"])
        for dim, stats in analysis["dimension_statistics"].items()
    ]
    high_variance_dims.sort(key=lambda x: x[1], reverse=True)

    report.append(f"\nMost common NPC pattern: {list(tag_freq.keys())[0]}")
    report.append(f"  ({most_common_tag} of {total_episodes} episodes, {100*most_common_tag/total_episodes:.1f}%)")

    report.append(f"\nMost variable dimension: {high_variance_dims[0][0]}")
    report.append(f"  (StDev = {high_variance_dims[0][1]:.2f})")
    report.append(f"\nLeast variable dimension: {high_variance_dims[-1][0]}")
    report.append(f"  (StDev = {high_variance_dims[-1][1]:.2f})")

    report.append("\nNext steps:")
    report.append("1. Review anchor candidate examples from above")
    report.append("2. Validate rubric scoring against real episodes")
    report.append("3. Adjust dimension weights if variance is extreme")
    report.append("4. Integrate automated audit into generation pipeline")
    report.append("5. Begin Phase 3: Build counterfactual test rig")

    return "\n".join(report)


def cmd_sample(count: int = 25):
    """Command: Sample episodes."""
    episodes = sample_episodes(count)

    # Save sample
    with open(CALIBRATION_RESULTS_PATH, 'w') as f:
        yaml.dump({
            "calibration_date": datetime.now().isoformat(),
            "sample_size": len(episodes),
            "sample": episodes,
            "audit_results": {},
        }, f, default_flow_style=False)

    print(f"✓ Sample saved to {CALIBRATION_RESULTS_PATH}")


def cmd_audit_sample():
    """Command: Audit sampled episodes."""
    audit_sample()


def cmd_analyze():
    """Command: Analyze calibration results."""
    analysis = analyze_calibration()

    report = generate_calibration_report(analysis)
    print(report)

    # Save analysis
    analysis_path = Path(__file__).parent / "calibration_analysis.yaml"
    with open(analysis_path, 'w') as f:
        yaml.dump(analysis, f, default_flow_style=False)

    print(f"\n✓ Analysis saved to {analysis_path}")


def cmd_report():
    """Command: Generate and display full report."""
    try:
        report = generate_calibration_report()
        print(report)
    except SystemExit:
        pass


def main():
    parser = argparse.ArgumentParser(description="Phase 2: Retrospective Calibration")
    subparsers = parser.add_subparsers(dest="command")

    sample_cmd = subparsers.add_parser("sample", help="Sample historical episodes")
    sample_cmd.add_argument("--count", type=int, default=25, help="Number to sample")

    subparsers.add_parser("audit-sample", help="Run audit on sampled episodes")
    subparsers.add_parser("analyze", help="Analyze calibration results")
    subparsers.add_parser("report", help="Display calibration report")

    args = parser.parse_args()

    if args.command == "sample":
        cmd_sample(args.count)
    elif args.command == "audit-sample":
        cmd_audit_sample()
    elif args.command == "analyze":
        cmd_analyze()
    elif args.command == "report":
        cmd_report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
