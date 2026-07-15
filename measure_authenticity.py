#!/usr/bin/env python3
"""Authenticity measurement and evaluation CLI.

Usage:
    python measure_authenticity.py init-db
    python measure_authenticity.py audit <episode_id>
    python measure_authenticity.py grade <episode_id> [--reviewer-type human|llm|automated]
    python measure_authenticity.py report <episode_id>
    python measure_authenticity.py calibrate
    python measure_authenticity.py benchmark-load
"""

import sys
import argparse
import json
from pathlib import Path

from episode_evaluation_db import (
    get_eval_connection, init_eval_db, create_episode_run,
    record_annotation, get_episode_annotations, aggregate_scores,
    add_benchmark_papers, get_benchmark_papers
)
from authenticity_audit import audit_episode
from grading_rubric import (
    aggregate_episode_score, check_release_gates,
    DIMENSION_ANCHORS
)
from db import get_connection, list_podcasts
import yaml


def cmd_init_db():
    """Initialize episode evaluation database."""
    conn = get_eval_connection()
    init_eval_db(conn)
    print("✓ Episode evaluation database initialized")
    conn.close()


def cmd_benchmark_load():
    """Load frozen benchmark set into database."""
    conn = get_eval_connection()
    init_eval_db(conn)

    with open(Path(__file__).parent / "frozen_benchmark_set.yaml") as f:
        data = yaml.safe_load(f)

    papers = data.get("benchmark_papers", [])
    add_benchmark_papers(conn, papers)

    print(f"✓ Loaded {len(papers)} benchmark papers")
    benchmarks = get_benchmark_papers(conn)
    for b in benchmarks:
        print(f"  {b['arxiv_id']}: {b['title'][:60]}...")

    conn.close()


def cmd_audit(episode_id: int):
    """Run automated audit on an episode."""
    # Load episode transcript
    conn = get_connection()
    episodes = list_podcasts(conn)
    conn.close()

    episode = None
    for ep in episodes:
        if ep["id"] == episode_id:
            episode = ep
            break

    if not episode:
        print(f"Episode {episode_id} not found")
        sys.exit(1)

    # For now, use description as transcript (in real usage, would be full transcript)
    transcript = episode.get("description", "")
    if not transcript:
        print(f"Episode {episode_id} has no transcript/description")
        sys.exit(1)

    lines = transcript.split('\n')
    results = audit_episode(transcript, lines)

    print(f"\n=== Audit Results for Episode {episode['id']}: {episode['title']} ===\n")

    print(f"Generic Openers: {len(results['generic_openers'])} instances")
    for pos, phrase in results['generic_openers'][:3]:
        print(f"  - {phrase}")

    print(f"\nPersona Declarations: {len(results['persona_declarations'])} instances")
    for pos, phrase in results['persona_declarations'][:3]:
        print(f"  - {phrase}")

    print(f"\nFailed Audit Checks: {', '.join(results['failure_tags'])}")
    print(f"Severity Count: {results['severity_count']}/10")

    return results


def cmd_grade(episode_id: int, reviewer_type: str = "automated"):
    """Grade an episode across all 7 dimensions."""
    # First run audit
    audit_results = cmd_audit(episode_id)

    print(f"\n=== Grading Episode {episode_id} ===\n")

    # Load scoring logic
    from grading_rubric import (
        score_evidence_contingency, score_character_contingency,
        score_conversational_causality, score_belief_continuity,
        score_agency_asymmetry, score_anti_caricature, score_naturalism
    )

    conn = get_connection()
    episodes = list_podcasts(conn)
    episode = next((e for e in episodes if e["id"] == episode_id), None)
    conn.close()

    if not episode:
        print(f"Episode {episode_id} not found")
        sys.exit(1)

    transcript = episode.get("description", "")
    lines = transcript.split('\n')

    # Score each dimension
    scores = {
        "evidence_score": score_evidence_contingency(transcript, audit_results),
        "character_score": score_character_contingency(transcript, audit_results),
        "conversation_score": score_conversational_causality(lines, {}),
        "belief_score": score_belief_continuity(transcript, audit_results),
        "agency_score": score_agency_asymmetry(audit_results),
        "anti_caricature_score": score_anti_caricature(transcript, audit_results),
        "naturalism_score": score_naturalism(transcript, audit_results),
    }

    print("Dimension Scores (0-4 scale):\n")
    for dim, score in sorted(scores.items()):
        dim_name = dim.replace("_score", "").replace("_", " ").title()
        bar = "█" * int(score) + "░" * (4 - int(score))
        print(f"  {dim_name:30} {bar} {score:.1f}")

    # Check release gates
    gates = check_release_gates(scores)
    print("\nRelease Gates:")
    for gate, passes in gates.items():
        status = "✓ PASS" if passes else "✗ FAIL"
        print(f"  {gate}: {status}")

    # Record in database
    eval_conn = get_eval_connection()
    init_eval_db(eval_conn)

    annotation_id = record_annotation(
        eval_conn,
        run_id=f"episode_{episode_id}",
        unit_type="episode",
        unit_id=str(episode_id),
        reviewer_id="cli_automated",
        reviewer_type=reviewer_type,
        scores=scores,
        confidence=0.7 if reviewer_type == "automated" else 0.9,
        failure_tags=audit_results["failure_tags"],
        notes=f"Automated audit: {audit_results['severity_count']} failures"
    )

    print(f"\n✓ Annotation recorded: {annotation_id}")
    eval_conn.close()


def cmd_report(episode_id: int):
    """Generate evaluation report for an episode."""
    eval_conn = get_eval_connection()

    annotations = get_episode_annotations(eval_conn, f"episode_{episode_id}")
    eval_conn.close()

    if not annotations:
        print(f"No annotations found for episode {episode_id}")
        sys.exit(1)

    print(f"\n=== Report for Episode {episode_id} ===\n")
    print(f"Annotations: {len(annotations)}")

    agg = aggregate_scores(annotations)
    print("\nAggregated Scores (median across reviewers):")
    for dim, score in sorted(agg.items()):
        dim_name = dim.replace("_score", "").replace("_", " ").title()
        print(f"  {dim_name:30} {score:.1f}")

    print("\nReviewer Details:")
    for ann in annotations:
        print(f"  {ann['reviewer_id']} ({ann['reviewer_type']}): confidence={ann['confidence']:.1f}")


def cmd_calibrate():
    """Interactive rubric calibration using benchmark papers.

    Guides through anchoring scenarios for each dimension.
    """
    eval_conn = get_eval_connection()
    benchmarks = get_benchmark_papers(eval_conn)
    eval_conn.close()

    print("\n=== Rubric Calibration Guide ===\n")
    print(f"Using {len(benchmarks)} benchmark papers.\n")

    for anchor in DIMENSION_ANCHORS:
        print(f"\n{anchor.dimension}")
        print("=" * 60)
        print(f"\nScore 0 (Worst):\n  {anchor.score_0}")
        print(f"\nScore 2 (Mixed):\n  {anchor.score_2}")
        print(f"\nScore 4 (Best):\n  {anchor.score_4}")
        print("\n" + "-" * 60)


def main():
    parser = argparse.ArgumentParser(description="Authenticity measurement system")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init-db", help="Initialize evaluation database")
    subparsers.add_parser("benchmark-load", help="Load frozen benchmark set")
    subparsers.add_parser("calibrate", help="Show rubric calibration guide")

    audit_cmd = subparsers.add_parser("audit", help="Run audit on episode")
    audit_cmd.add_argument("episode_id", type=int, help="Episode ID")

    grade_cmd = subparsers.add_parser("grade", help="Grade episode across dimensions")
    grade_cmd.add_argument("episode_id", type=int, help="Episode ID")
    grade_cmd.add_argument("--reviewer-type", default="automated", help="Reviewer type")

    report_cmd = subparsers.add_parser("report", help="Generate evaluation report")
    report_cmd.add_argument("episode_id", type=int, help="Episode ID")

    args = parser.parse_args()

    if args.command == "init-db":
        cmd_init_db()
    elif args.command == "benchmark-load":
        cmd_benchmark_load()
    elif args.command == "calibrate":
        cmd_calibrate()
    elif args.command == "audit":
        cmd_audit(args.episode_id)
    elif args.command == "grade":
        cmd_grade(args.episode_id, args.reviewer_type)
    elif args.command == "report":
        cmd_report(args.episode_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
