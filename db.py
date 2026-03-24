"""SQLite cache for paper deduplication, storage, and podcast tracking."""

import re
import sqlite3
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "papers.db"
LEGACY_FEED_PATH = Path(__file__).parent / "podcasts" / "anchor_feed.xml"
ARXIV_ID_RE = re.compile(r'(\d{4}\.\d{4,5})(?:v\d+)?')


def _extract_arxiv_ids_from_text(text):
    if not text:
        return set()
    return {m.group(1) for m in ARXIV_ID_RE.finditer(text)}


def _get_legacy_episode_arxiv_ids(feed_path=None):
    """Extract arXiv IDs from legacy Anchor feed descriptions/links."""
    path = Path(feed_path or LEGACY_FEED_PATH)
    if not path.exists():
        return set()
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return set()

    ids = set()
    for item in tree.findall('./channel/item'):
        for tag in ('description', 'link', 'guid'):
            elem = item.find(tag)
            if elem is not None and elem.text:
                ids.update(_extract_arxiv_ids_from_text(elem.text))
    return ids


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS covered_topics (
            topic TEXT PRIMARY KEY,
            first_covered TEXT NOT NULL,
            episode_count INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fun_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            source TEXT,
            collected_at TEXT NOT NULL,
            used_at TEXT,
            used_in_episode TEXT,
            status TEXT NOT NULL DEFAULT 'unused'
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
    valid_fields = {"title", "publish_date", "spotify_url", "elevenlabs_project_id",
                     "audio_file", "description", "image_file"}
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


def get_all_episode_arxiv_ids(conn):
    """Get arXiv IDs from both podcast_papers and podcasts.source_urls.

    The podcast_papers junction table only covers digest-sourced episodes.
    URL-based episodes store paper URLs in the source_urls JSON column.
    This function unions both sources for complete dedup.
    """
    ids = get_podcast_arxiv_ids(conn)
    rows = conn.execute(
        "SELECT source_urls FROM podcasts "
        "WHERE source_urls IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            for url in json.loads(row["source_urls"]):
                ids.update(_extract_arxiv_ids_from_text(url))
        except (json.JSONDecodeError, TypeError):
            pass
    ids.update(_get_legacy_episode_arxiv_ids())
    return ids


def get_episodes_by_arxiv_id(conn):
    """Map each arXiv ID to the list of episodes that reference it.

    Combines podcast_papers junction table entries, arXiv IDs
    extracted from the source_urls JSON column, and legacy feed
    episodes from anchor_feed.xml.
    """
    from collections import defaultdict
    episodes = list_podcasts(conn)
    mapping = defaultdict(list)
    for ep in episodes:
        aids = set()
        paper_ids = ep.get("paper_ids")
        if paper_ids:
            for aid in paper_ids.split(","):
                aid = aid.strip()
                if aid:
                    aids.add(aid)
        source_urls = ep.get("source_urls")
        if source_urls:
            try:
                for url in json.loads(source_urls):
                    aids.update(_extract_arxiv_ids_from_text(url))
            except (json.JSONDecodeError, TypeError):
                pass
        for aid in aids:
            mapping[aid].append(ep)

    if LEGACY_FEED_PATH.exists():
        try:
            tree = ET.parse(LEGACY_FEED_PATH)
            for item in tree.findall('./channel/item'):
                aids = set()
                title = item.findtext('title', default='')
                link = item.findtext('link', default='')
                description = item.findtext('description', default='')
                pub_date = item.findtext('pubDate', default='')
                for text in (title, link, description):
                    aids.update(_extract_arxiv_ids_from_text(text))
                if not aids:
                    continue
                episode = {
                    'title': title,
                    'publish_date': pub_date,
                    'audio_file': item.findtext('enclosure', default=''),
                    'description': description,
                    'legacy': True,
                    'link': link,
                }
                for aid in aids:
                    mapping[aid].append(episode)
        except ET.ParseError:
            pass
    return mapping


def get_covered_topics(conn):
    rows = conn.execute("SELECT topic FROM covered_topics").fetchall()
    return {row["topic"] for row in rows}


def add_covered_topics(conn, topics):
    for t in topics:
        conn.execute("""
            INSERT INTO covered_topics (topic, first_covered, episode_count)
            VALUES (?, ?, 1)
            ON CONFLICT(topic) DO UPDATE SET episode_count = episode_count + 1
        """, (t, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def get_unused_fun_facts(conn, limit=10, category=None):
    """Get unused fun facts, optionally filtered by category."""
    if category:
        rows = conn.execute(
            "SELECT * FROM fun_facts WHERE status = 'unused' AND category = ? "
            "ORDER BY collected_at DESC LIMIT ?", (category, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM fun_facts WHERE status = 'unused' "
            "ORDER BY collected_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_facts_used(conn, fact_ids, episode_title=""):
    """Mark fun facts as used."""
    for fid in fact_ids:
        conn.execute(
            "UPDATE fun_facts SET status = 'used', used_at = ?, used_in_episode = ? "
            "WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), episode_title, fid)
        )
    conn.commit()


def add_fun_facts(conn, facts):
    """Add new fun facts. Each fact is a dict with: fact, category, source."""
    for f in facts:
        # Check for duplicates
        existing = conn.execute(
            "SELECT 1 FROM fun_facts WHERE fact = ?", (f["fact"],)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO fun_facts (fact, category, source, collected_at) "
            "VALUES (?, ?, ?, ?)",
            (f["fact"], f.get("category", "general"), f.get("source", ""),
             datetime.now(timezone.utc).isoformat())
        )
    conn.commit()


def get_episode_count(conn):
    """Get total number of podcast episodes."""
    row = conn.execute("SELECT COUNT(*) FROM podcasts").fetchone()
    return row[0] if row else 0


def prune_used_fun_facts(conn, keep_days=7):
    """Delete used fun facts older than keep_days. Keeps the DB lean."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    result = conn.execute(
        "DELETE FROM fun_facts WHERE status = 'used' AND used_at < ?", (cutoff,)
    )
    deleted = result.rowcount
    conn.commit()
    return deleted


def get_fun_facts_stats(conn):
    """Get counts of used/unused fun facts."""
    unused = conn.execute("SELECT COUNT(*) FROM fun_facts WHERE status='unused'").fetchone()[0]
    used = conn.execute("SELECT COUNT(*) FROM fun_facts WHERE status='used'").fetchone()[0]
    return {"unused": unused, "used": used, "total": unused + used}


def list_podcasts(conn):
    rows = conn.execute("""
        SELECT p.*, GROUP_CONCAT(pp.arxiv_id) as paper_ids
        FROM podcasts p
        LEFT JOIN podcast_papers pp ON p.id = pp.podcast_id
        GROUP BY p.id
        ORDER BY p.publish_date DESC, p.created_at DESC
    """).fetchall()
    return [dict(row) for row in rows]
