"""SQLite cache for paper deduplication, storage, and podcast tracking."""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "papers.db"


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT,
            abstract TEXT,
            categories TEXT,
            arxiv_url TEXT,
            published TEXT,
            score REAL,
            score_reason TEXT,
            hf_daily INTEGER DEFAULT 0,
            citation_count INTEGER DEFAULT 0,
            citation_velocity REAL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            digest_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            publish_date TEXT NOT NULL,
            spotify_url TEXT,
            elevenlabs_project_id TEXT,
            audio_file TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS podcast_papers (
            podcast_id INTEGER NOT NULL,
            arxiv_id TEXT NOT NULL,
            PRIMARY KEY (podcast_id, arxiv_id),
            FOREIGN KEY (podcast_id) REFERENCES podcasts(id),
            FOREIGN KEY (arxiv_id) REFERENCES papers(arxiv_id)
        )
    """)
    # Migration: add source_urls column if it doesn't exist yet
    try:
        conn.execute("ALTER TABLE podcasts ADD COLUMN source_urls TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migration: add description column if it doesn't exist yet
    try:
        conn.execute("ALTER TABLE podcasts ADD COLUMN description TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Migration: add image_file column if it doesn't exist yet
    try:
        conn.execute("ALTER TABLE podcasts ADD COLUMN image_file TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()


def paper_exists(conn, arxiv_id):
    row = conn.execute(
        "SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    return row is not None


def upsert_paper(conn, paper):
    conn.execute("""
        INSERT INTO papers (
            arxiv_id, title, authors, abstract, categories,
            arxiv_url, published, score, score_reason,
            hf_daily, citation_count, citation_velocity,
            fetched_at, digest_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            score = excluded.score,
            score_reason = excluded.score_reason,
            hf_daily = excluded.hf_daily,
            citation_count = excluded.citation_count,
            citation_velocity = excluded.citation_velocity,
            digest_date = excluded.digest_date
    """, (
        paper["arxiv_id"],
        paper["title"],
        json.dumps(paper.get("authors", [])),
        paper.get("abstract", ""),
        json.dumps(paper.get("categories", [])),
        paper.get("arxiv_url", ""),
        paper.get("published", ""),
        paper.get("score", 0.0),
        paper.get("score_reason", ""),
        1 if paper.get("hf_daily") else 0,
        paper.get("citation_count", 0),
        paper.get("citation_velocity", 0.0),
        datetime.now(timezone.utc).isoformat(),
        paper.get("digest_date"),
    ))
    conn.commit()


def get_today_papers(conn, date_str=None):
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM papers WHERE digest_date = ? ORDER BY score DESC",
        (date_str,)
    ).fetchall()
    papers = []
    for row in rows:
        p = dict(row)
        p["authors"] = json.loads(p["authors"]) if p["authors"] else []
        p["categories"] = json.loads(p["categories"]) if p["categories"] else []
        papers.append(p)
    return papers


def insert_podcast(conn, title, publish_date, elevenlabs_project_id=None,
                   audio_file=None, spotify_url=None, source_urls=None,
                   description=None, image_file=None):
    cursor = conn.execute("""
        INSERT INTO podcasts (title, publish_date, spotify_url,
                              elevenlabs_project_id, audio_file, source_urls,
                              description, image_file, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        publish_date,
        spotify_url,
        elevenlabs_project_id,
        audio_file,
        source_urls,
        description,
        image_file,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    return cursor.lastrowid


def update_podcast(conn, podcast_id, **kwargs):
    valid_fields = {"title", "spotify_url", "elevenlabs_project_id", "audio_file",
                     "description", "image_file"}
    updates = {k: v for k, v in kwargs.items() if k in valid_fields}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [podcast_id]
    conn.execute(f"UPDATE podcasts SET {set_clause} WHERE id = ?", values)
    conn.commit()


def link_podcast_paper(conn, podcast_id, arxiv_id):
    conn.execute("""
        INSERT OR IGNORE INTO podcast_papers (podcast_id, arxiv_id)
        VALUES (?, ?)
    """, (podcast_id, arxiv_id))
    conn.commit()


def get_podcast_arxiv_ids(conn):
    rows = conn.execute("SELECT DISTINCT arxiv_id FROM podcast_papers").fetchall()
    return {row["arxiv_id"] for row in rows}


def list_podcasts(conn):
    rows = conn.execute("""
        SELECT p.*, GROUP_CONCAT(pp.arxiv_id) as paper_ids
        FROM podcasts p
        LEFT JOIN podcast_papers pp ON p.id = pp.podcast_id
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """).fetchall()
    return [dict(row) for row in rows]
