#!/usr/bin/env python3
"""Phase 4: Active Optimization Loop

Monitor authenticity scores, track regressions, identify underperforming
dimensions, and iterate SOUL policies toward higher authenticity.

Usage:
    python phase4_active_optimization.py measure <episode_id>
    python phase4_active_optimization.py batch-measure [--count 10]
    python phase4_active_optimization.py monitor
    python phase4_active_optimization.py trend <dimension>
    python phase4_active_optimization.py suggest-iteration
    python phase4_active_optimization.py benchmark-check
    python phase4_active_optimization.py report
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

from db import get_connection, list_podcasts
from episode_evaluation_db import (
    get_eval_connection, init_eval_db, create_episode_run,
    record_annotation, get_episode_annotations, aggregate_scores,
    get_benchmark_papers
)
from authenticity_audit import audit_episode
from grading_rubric import (
    score_evidence_contingency, score_character_contingency,
    score_conversational_causality, score_belief_continuity,
    score_agency_asymmetry, score_anti_caricature, score_naturalism,
)
from soul_loader import get_soul_profile
import yaml


MONITORING_DB_PATH = Path(__file__).parent / "optimization_monitoring.yaml"
ITERATION_LOG_PATH = Path(__file__).parent / "soul_iteration_log.yaml"


def measure_episode(episode_id: int, episode_data: dict = None) -> dict:
    """Measure authenticity of a single episode.

    Args:
        episode_id: Podcast episode ID
        episode_data: Episode dict (if None, fetches from DB)

    Returns:
        Dict with scores, tags, timestamp
    """
    if episode_data is None:
        conn = get_connection()
        episodes = list_podcasts(conn)
        conn.close()
        episode_data = next((e for e in episodes if e["id"] == episode_id), None)

    if not episode_data:
        print(f"Episode {episode_id} not found")
        return None

    transcript = episode_data.get("description", "")
    if not transcript:
        return None

    # Audit
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
    eval_conn = get_eval_connection()
    init_eval_db(eval_conn)

    run_id = f"production_ep_{episode_id}"
    record_annotation(
        eval_conn,
        run_id=run_id,
        unit_type="episode",
        unit_id=str(episode_id),
        reviewer_id="phase4_automated",
        reviewer_type="automated",
        scores=scores,
        confidence=0.7,
        failure_tags=audit_results["failure_tags"],
        notes=f"Production monitoring: {audit_results['severity_count']} audit failures"
    )

    eval_conn.close()

    # Aggregate score
    avg_score = sum(scores.values()) / len(scores)

    return {
        "episode_id": episode_id,
        "title": episode_data.get("title", ""),
        "publish_date": episode_data.get("publish_date", ""),
        "measured_at": datetime.now().isoformat(),
        "scores": scores,
        "avg_score": avg_score,
        "failure_tags": audit_results["failure_tags"],
        "severity_count": audit_results["severity_count"],
    }


def batch_measure_episodes(count: int = 10) -> list:
    """Measure the most recent episodes.

    Args:
        count: Number of recent episodes to measure

    Returns:
        List of measurement results
    """
    conn = get_connection()
    episodes = list_podcasts(conn)
    conn.close()

    # Sort by publish date, take most recent
    sorted_eps = sorted(
        episodes,
        key=lambda e: e.get("publish_date", ""),
        reverse=True
    )[:count]

    print(f"Measuring {len(sorted_eps)} recent episodes...\n")

    results = []
    for i, ep in enumerate(sorted_eps, 1):
        result = measure_episode(ep["id"], ep)
        if result:
            results.append(result)
            avg = result["avg_score"]
            bar = "█" * int(avg) + "░" * (4 - int(avg))
            print(f"  [{i:2d}] {ep['id']:3d} {bar} {avg:.1f} | {ep['title'][:40]}")

    # Save results
    with open(MONITORING_DB_PATH, 'a') as f:
        for result in results:
            f.write(yaml.dump(result) + "---\n")

    print(f"\n✓ Measured {len(results)} episodes")
    return results


def get_monitoring_history() -> list:
    """Load all monitoring measurements."""
    if not MONITORING_DB_PATH.exists():
        return []

    with open(MONITORING_DB_PATH) as f:
        content = f.read()

    # Parse YAML documents
    measurements = []
    for doc in content.split("---\n"):
        if doc.strip():
            try:
                m = yaml.safe_load(doc)
                if m:
                    measurements.append(m)
            except:
                pass

    return measurements


def analyze_trends(measurements: list = None) -> dict:
    """Analyze score trends over time."""
    if measurements is None:
        measurements = get_monitoring_history()

    if not measurements:
        return {}

    # Sort by timestamp
    sorted_m = sorted(
        measurements,
        key=lambda m: m.get("measured_at", "")
    )

    # Aggregate by dimension
    dimension_series = defaultdict(list)
    for m in sorted_m:
        timestamp = m.get("measured_at", "")
        scores = m.get("scores", {})

        for dim, score in scores.items():
            dimension_series[dim].append({
                "timestamp": timestamp,
                "score": score,
            })

    # Compute trends
    trends = {}
    for dim, series in dimension_series.items():
        if len(series) < 2:
            continue

        scores = [s["score"] for s in series]
        trend = {
            "dimension": dim,
            "count": len(scores),
            "mean": statistics.mean(scores),
            "median": statistics.median(scores),
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0,
            "latest": scores[-1],
            "first": scores[0],
            "delta": scores[-1] - scores[0],  # Positive = improvement
        }

        # Trend direction (last 5 vs first 5)
        if len(scores) >= 10:
            recent_mean = statistics.mean(scores[-5:])
            early_mean = statistics.mean(scores[:5])
            trend["recent_vs_early"] = recent_mean - early_mean
        else:
            trend["recent_vs_early"] = None

        trends[dim] = trend

    return trends


def suggest_iteration(measurements: list = None) -> dict:
    """Suggest SOUL policy changes based on underperforming dimensions."""
    if measurements is None:
        measurements = get_monitoring_history()

    trends = analyze_trends(measurements)

    if not trends:
        return {"error": "Not enough data for suggestions"}

    # Identify underperforming dimensions
    suggestions = []

    for dim, trend in sorted(trends.items(), key=lambda x: x[1]["mean"]):
        if trend["mean"] < 2.0:  # Below neutral
            if dim == "evidence_score":
                suggestions.append({
                    "dimension": "Evidence Contingency",
                    "problem": f"Score {trend['mean']:.1f} (should be 3+)",
                    "layer": "LENS_POLICY",
                    "action": "Increase evidence_drives; guide hosts to ground reasoning in specific paper claims",
                    "priority": "CRITICAL",
                })

            elif dim == "character_score":
                suggestions.append({
                    "dimension": "Character-Contingent Appraisal",
                    "problem": f"Score {trend['mean']:.1f} (should be 3+)",
                    "layer": "LENS_POLICY or CONVERSATION_POLICY",
                    "action": "Increase distinct evidence selection per host; test SOUL lens differences",
                    "priority": "HIGH",
                })

            elif dim == "belief_score":
                suggestions.append({
                    "dimension": "Belief Continuity",
                    "problem": f"Score {trend['mean']:.1f} (should be 3+)",
                    "layer": "CONVERSATION_POLICY",
                    "action": "Add explicit belief-update instructions; hosts must reference prior reasoning",
                    "priority": "HIGH",
                })

            elif dim == "naturalism_score":
                suggestions.append({
                    "dimension": "Naturalism",
                    "problem": f"Score {trend['mean']:.1f} (should be 3+)",
                    "layer": "VOICE_REALIZATION",
                    "action": "Reduce template markers; vary sentence structure and pacing per host",
                    "priority": "MEDIUM",
                })

    return {
        "timestamp": datetime.now().isoformat(),
        "measurement_count": len(measurements),
        "underperforming_dimensions": len(suggestions),
        "suggestions": suggestions,
    }


def check_benchmark_regression(measurements: list = None) -> dict:
    """Check for regression against frozen benchmark set."""
    eval_conn = get_eval_connection()
    benchmarks = get_benchmark_papers(eval_conn)
    eval_conn.close()

    if not benchmarks:
        return {"error": "Benchmark set not loaded"}

    if measurements is None:
        measurements = get_monitoring_history()

    # Find measurements for benchmark papers
    benchmark_ids = {b["arxiv_id"] for b in benchmarks}
    benchmark_measurements = [
        m for m in measurements
        if any(bid in m.get("title", "") for bid in benchmark_ids)
    ]

    if not benchmark_measurements:
        return {"warning": "No benchmark measurements yet"}

    # Compute baseline (first 3 measurements per benchmark)
    baseline = defaultdict(list)
    for m in benchmark_measurements[:5]:  # First batch
        for dim, score in m.get("scores", {}).items():
            baseline[dim].append(score)

    # Compute current (last 3 measurements per benchmark)
    current = defaultdict(list)
    for m in benchmark_measurements[-5:]:  # Last batch
        for dim, score in m.get("scores", {}).items():
            current[dim].append(score)

    # Compare
    regressions = []
    improvements = []

    for dim in baseline:
        if not current.get(dim):
            continue

        baseline_mean = statistics.mean(baseline[dim])
        current_mean = statistics.mean(current[dim])
        delta = current_mean - baseline_mean

        if delta < -0.3:  # Regression of 0.3+ points
            regressions.append({
                "dimension": dim,
                "baseline": baseline_mean,
                "current": current_mean,
                "delta": delta,
                "severity": "ALERT" if delta < -0.5 else "WARNING",
            })

        elif delta > 0.3:  # Improvement
            improvements.append({
                "dimension": dim,
                "baseline": baseline_mean,
                "current": current_mean,
                "delta": delta,
            })

    return {
        "timestamp": datetime.now().isoformat(),
        "measurements_analyzed": len(benchmark_measurements),
        "regressions": regressions,
        "improvements": improvements,
        "status": "ALERT" if regressions else "OK",
    }


def cmd_measure(episode_id: int):
    """Command: Measure single episode."""
    result = measure_episode(episode_id)
    if result:
        print("\n" + "=" * 70)
        print(f"Episode {episode_id}: {result['title']}")
        print("=" * 70)
        print("\nScores:")
        for dim, score in result["scores"].items():
            dim_name = dim.replace("_score", "").replace("_", " ").title()
            bar = "█" * int(score) + "░" * (4 - int(score))
            print(f"  {dim_name:30s} {bar} {score:.1f}")

        if result["failure_tags"]:
            print(f"\nFailure tags: {', '.join(result['failure_tags'])}")

        print("=" * 70)


def cmd_batch_measure(count: int = 10):
    """Command: Measure batch of recent episodes."""
    batch_measure_episodes(count)


def cmd_monitor():
    """Command: Display monitoring dashboard."""
    measurements = get_monitoring_history()
    trends = analyze_trends(measurements)

    print("\n" + "=" * 70)
    print("AUTHENTICITY MONITORING DASHBOARD")
    print("=" * 70)
    print(f"\nMeasurements: {len(measurements)} episodes tracked\n")

    print("Dimension Performance:")
    print("(Sorted by mean score, ascending)\n")

    for dim, trend in sorted(trends.items(), key=lambda x: x[1]["mean"]):
        dim_name = dim.replace("_score", "").replace("_", " ").title()
        bar = "█" * int(trend["mean"]) + "░" * (4 - int(trend["mean"]))
        arrow = "↑" if trend.get("delta", 0) > 0 else "↓" if trend.get("delta", 0) < 0 else "→"

        print(f"  {dim_name:30s} {bar} {trend['mean']:.1f} {arrow:1s} {trend['delta']:+.1f}")

    print("\n" + "=" * 70)


def cmd_trend(dimension: str):
    """Command: Show trend for a dimension."""
    measurements = get_monitoring_history()
    trends = analyze_trends(measurements)

    # Find matching dimension
    matching_dim = None
    for dim in trends:
        if dimension.lower() in dim.lower():
            matching_dim = dim
            break

    if not matching_dim:
        print(f"Dimension '{dimension}' not found")
        return

    trend = trends[matching_dim]
    dim_name = matching_dim.replace("_score", "").replace("_", " ").title()

    print(f"\n{dim_name} — Trend Analysis\n")
    print(f"  Measurements: {trend['count']}")
    print(f"  Mean: {trend['mean']:.2f}")
    print(f"  Median: {trend['median']:.2f}")
    print(f"  StDev: {trend['stdev']:.2f}")
    print(f"  Latest: {trend['latest']:.1f}")
    print(f"  First: {trend['first']:.1f}")
    print(f"  Delta: {trend['delta']:+.1f}")

    if trend.get("recent_vs_early"):
        print(f"  Trend (last 5 vs first 5): {trend['recent_vs_early']:+.1f}")

    print()


def cmd_suggest_iteration():
    """Command: Suggest SOUL policy iterations."""
    suggestion = suggest_iteration()

    if "error" in suggestion:
        print(f"Error: {suggestion['error']}")
        return

    print("\n" + "=" * 70)
    print("SOUL POLICY ITERATION SUGGESTIONS")
    print("=" * 70)
    print(f"\nBased on {suggestion['measurement_count']} measurements\n")

    if not suggestion["suggestions"]:
        print("✓ All dimensions performing well (mean >= 2.0)")
        print("\nNext: Run counterfactual test to validate drives improve scores further")
        print("=" * 70)
        return

    print(f"Found {len(suggestion['suggestions'])} underperforming dimensions:\n")

    for s in suggestion["suggestions"]:
        print(f"{s['priority']}: {s['dimension']}")
        print(f"  Problem: {s['problem']}")
        print(f"  Layer: {s['layer']}")
        print(f"  Action: {s['action']}")
        print()

    print("=" * 70)

    # Save suggestion to iteration log
    with open(ITERATION_LOG_PATH, 'a') as f:
        f.write(yaml.dump(suggestion) + "---\n")


def cmd_benchmark_check():
    """Command: Check for benchmark regression."""
    check = check_benchmark_regression()

    print("\n" + "=" * 70)
    print("BENCHMARK REGRESSION CHECK")
    print("=" * 70)

    if "error" in check or "warning" in check:
        print(f"\n{check.get('error') or check.get('warning')}\n")
        print("=" * 70)
        return

    print(f"\nAnalyzed: {check['measurements_analyzed']} measurements\n")

    if check["regressions"]:
        print(f"🚨 REGRESSIONS DETECTED ({len(check['regressions'])}):\n")
        for r in check["regressions"]:
            dim = r["dimension"].replace("_score", "").title()
            print(f"  {dim}: {r['baseline']:.1f} → {r['current']:.1f} ({r['delta']:+.1f}) [{r['severity']}]")
        print()
    else:
        print("✓ No regressions\n")

    if check["improvements"]:
        print(f"✨ IMPROVEMENTS ({len(check['improvements'])}):\n")
        for i in check["improvements"]:
            dim = i["dimension"].replace("_score", "").title()
            print(f"  {dim}: {i['baseline']:.1f} → {i['current']:.1f} ({i['delta']:+.1f})")
        print()

    print("=" * 70)


def cmd_report():
    """Command: Generate optimization report."""
    measurements = get_monitoring_history()
    trends = analyze_trends(measurements)
    suggestions = suggest_iteration(measurements)
    check = check_benchmark_regression(measurements)

    print("\n" + "=" * 70)
    print("PHASE 4: ACTIVE OPTIMIZATION REPORT")
    print("=" * 70)

    print(f"\nMeasurements: {len(measurements)} episodes")
    print(f"Date range: {measurements[0].get('measured_at', '')[:10]} to "
          f"{measurements[-1].get('measured_at', '')[:10]}" if measurements else "")

    print("\n" + "-" * 70)
    print("PERFORMANCE SUMMARY")
    print("-" * 70)

    for dim, trend in sorted(trends.items(), key=lambda x: x[1]["mean"], reverse=True):
        bar = "█" * int(trend["mean"]) + "░" * (4 - int(trend["mean"]))
        print(f"  {dim.replace('_score', '').title():30s} {bar} {trend['mean']:.1f}")

    if check.get("regressions"):
        print("\n" + "-" * 70)
        print("⚠️  REGRESSIONS")
        print("-" * 70)
        for r in check["regressions"]:
            print(f"  {r['dimension']}: {r['delta']:+.1f} [{r['severity']}]")

    if suggestions.get("suggestions"):
        print("\n" + "-" * 70)
        print("SUGGESTED ITERATIONS")
        print("-" * 70)
        for s in suggestions["suggestions"][:3]:  # Top 3
            print(f"  [{s['priority']}] {s['dimension']}: {s['action'][:50]}...")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Phase 4: Active Optimization")
    subparsers = parser.add_subparsers(dest="command")

    measure_cmd = subparsers.add_parser("measure", help="Measure single episode")
    measure_cmd.add_argument("episode_id", type=int)

    batch_cmd = subparsers.add_parser("batch-measure", help="Measure recent episodes")
    batch_cmd.add_argument("--count", type=int, default=10)

    subparsers.add_parser("monitor", help="Display monitoring dashboard")
    subparsers.add_parser("suggest-iteration", help="Suggest SOUL policy changes")
    subparsers.add_parser("benchmark-check", help="Check for regressions")
    subparsers.add_parser("report", help="Full optimization report")

    trend_cmd = subparsers.add_parser("trend", help="Analyze dimension trend")
    trend_cmd.add_argument("dimension", help="Dimension name")

    args = parser.parse_args()

    if args.command == "measure":
        cmd_measure(args.episode_id)
    elif args.command == "batch-measure":
        cmd_batch_measure(args.count)
    elif args.command == "monitor":
        cmd_monitor()
    elif args.command == "trend":
        cmd_trend(args.dimension)
    elif args.command == "suggest-iteration":
        cmd_suggest_iteration()
    elif args.command == "benchmark-check":
        cmd_benchmark_check()
    elif args.command == "report":
        cmd_report()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
