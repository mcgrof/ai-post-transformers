"""Episode evaluation database schema for authenticity measurement system."""

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

EVAL_DB_PATH = Path(__file__).parent / "episode_evaluation.db"


def get_eval_connection(db_path=None):
    """Get connection to episode evaluation database."""
    conn = sqlite3.connect(db_path or EVAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_eval_db(conn):
    """Initialize episode evaluation schema."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS episode_run (
            run_id TEXT PRIMARY KEY,
            published_episode_id INTEGER,
            paper_ids TEXT,
            generation_date TEXT NOT NULL,
            host_set TEXT,
            model_id TEXT,
            pipeline_version TEXT,
            prompt_version TEXT,
            soul_version_hal TEXT,
            soul_version_ada TEXT,
            soul_version_vera TEXT,
            random_seed INTEGER,
            fallback_mode TEXT,
            artifact_stage TEXT,
            selected_for_publication INTEGER DEFAULT 0,
            human_edit_minutes REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS drive_activation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            host TEXT NOT NULL,
            drive_id TEXT NOT NULL,
            activation_source TEXT,
            activation_strength REAL,
            evidence_ids TEXT,
            appraisal_valence TEXT,
            FOREIGN KEY (run_id) REFERENCES episode_run(run_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS annotation (
            annotation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            unit_type TEXT,
            unit_id TEXT,
            reviewer_id TEXT NOT NULL,
            reviewer_type TEXT,
            evidence_score INTEGER,
            character_score INTEGER,
            conversation_score INTEGER,
            belief_score INTEGER,
            agency_score INTEGER,
            anti_caricature_score INTEGER,
            naturalism_score INTEGER,
            confidence REAL,
            failure_tags TEXT,
            freeform_notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES episode_run(run_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiment (
            experiment_id TEXT PRIMARY KEY,
            hypothesis TEXT NOT NULL,
            control_pipeline_version TEXT,
            treatment_pipeline_version TEXT,
            frozen_paper_set TEXT,
            preregistered_primary_metrics TEXT,
            preregistered_failure_gates TEXT,
            result_summary TEXT,
            promotion_decision TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_paper (
            paper_id TEXT PRIMARY KEY,
            arxiv_id TEXT,
            title TEXT NOT NULL,
            paper_type TEXT,
            difficulty_level TEXT,
            reason_selected TEXT,
            added_at TEXT NOT NULL
        )
    """)

    conn.commit()


def create_episode_run(conn, paper_ids, model_id, pipeline_version,
                       prompt_version, soul_versions, host_set=None,
                       artifact_stage="raw_generation"):
    """Create a new episode run record for measurement.

    Args:
        conn: Database connection
        paper_ids: List of arXiv IDs
        model_id: Model name (e.g., "claude-opus-4-8")
        pipeline_version: Pipeline version string
        prompt_version: Prompt template version
        soul_versions: Dict with hal, ada, vera versions
        host_set: List of host names
        artifact_stage: Generation stage

    Returns:
        run_id UUID string
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO episode_run (
            run_id, paper_ids, generation_date, host_set,
            model_id, pipeline_version, prompt_version,
            soul_version_hal, soul_version_ada, soul_version_vera,
            artifact_stage, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id,
        json.dumps(paper_ids),
        now,
        json.dumps(host_set or ["Hal", "Ada", "VERA"]),
        model_id,
        pipeline_version,
        prompt_version,
        soul_versions.get("hal"),
        soul_versions.get("ada"),
        soul_versions.get("vera"),
        artifact_stage,
        now,
    ))
    conn.commit()
    return run_id


def record_annotation(conn, run_id, unit_type, unit_id, reviewer_id,
                      reviewer_type, scores, confidence=0.8,
                      failure_tags=None, notes=""):
    """Record a single annotation (utterance, scene, or episode).

    Args:
        conn: Database connection
        run_id: Episode run ID
        unit_type: "utterance", "scene", or "episode"
        unit_id: Identifier for the unit
        reviewer_id: Who reviewed (user email, "automated", etc)
        reviewer_type: "technical", "character", "naive_listener", "automated"
        scores: Dict with keys: evidence, character, conversation, belief,
                agency, anti_caricature, naturalism (each 0-4)
        confidence: Reviewer confidence 0-1
        failure_tags: List of failure tag strings
        notes: Free-form notes

    Returns:
        annotation_id UUID
    """
    annotation_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO annotation (
            annotation_id, run_id, unit_type, unit_id,
            reviewer_id, reviewer_type,
            evidence_score, character_score, conversation_score, belief_score,
            agency_score, anti_caricature_score, naturalism_score,
            confidence, failure_tags, freeform_notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        annotation_id, run_id, unit_type, unit_id,
        reviewer_id, reviewer_type,
        scores.get("evidence", 0),
        scores.get("character", 0),
        scores.get("conversation", 0),
        scores.get("belief", 0),
        scores.get("agency", 0),
        scores.get("anti_caricature", 0),
        scores.get("naturalism", 0),
        confidence,
        json.dumps(failure_tags or []),
        notes,
        now,
    ))
    conn.commit()
    return annotation_id


def get_episode_annotations(conn, run_id, unit_type=None):
    """Retrieve all annotations for an episode run."""
    if unit_type:
        rows = conn.execute("""
            SELECT * FROM annotation
            WHERE run_id = ? AND unit_type = ?
            ORDER BY created_at ASC
        """, (run_id, unit_type)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM annotation
            WHERE run_id = ?
            ORDER BY created_at ASC
        """, (run_id,)).fetchall()
    return [dict(row) for row in rows]


def aggregate_scores(annotations):
    """Aggregate multiple annotations into median scores per dimension."""
    if not annotations:
        return {}

    dimensions = [
        "evidence_score", "character_score", "conversation_score",
        "belief_score", "agency_score", "anti_caricature_score",
        "naturalism_score"
    ]
    aggregated = {}

    for dim in dimensions:
        scores = sorted([a[dim] for a in annotations if a[dim] is not None])
        if scores:
            mid = len(scores) // 2
            aggregated[dim] = scores[mid] if len(scores) % 2 == 1 else (scores[mid-1] + scores[mid]) / 2

    return aggregated


def add_benchmark_papers(conn, papers):
    """Add papers to the frozen benchmark set.

    Args:
        conn: Database connection
        papers: List of dicts with arxiv_id, title, paper_type,
                difficulty_level, reason_selected
    """
    now = datetime.now(timezone.utc).isoformat()
    for p in papers:
        paper_id = str(uuid.uuid4())
        conn.execute("""
            INSERT OR IGNORE INTO benchmark_paper (
                paper_id, arxiv_id, title, paper_type,
                difficulty_level, reason_selected, added_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_id,
            p.get("arxiv_id", ""),
            p.get("title", ""),
            p.get("paper_type", ""),
            p.get("difficulty_level", ""),
            p.get("reason_selected", ""),
            now,
        ))
    conn.commit()


def get_benchmark_papers(conn):
    """Retrieve frozen benchmark set."""
    rows = conn.execute("SELECT * FROM benchmark_paper ORDER BY added_at ASC").fetchall()
    return [dict(row) for row in rows]
