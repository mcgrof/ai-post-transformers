"""Draft revision tracking for logical podcast episodes.

A logical episode represents one conceptual podcast topic. When
the same topic is regenerated, the new draft becomes a numbered
revision (v2, v3, ...) of the same logical episode rather than an
unrelated draft.

The grouping key is derived from the episode title via
normalize_episode_key(). Source URLs provide a secondary match
signal for multi-paper episodes.
"""

import json
import re
import unicodedata


# ── Episode key normalization ──────────────────────────────────

def normalize_episode_key(title):
    """Derive a stable grouping key from an episode title.

    Strips the "Episode: " prefix, normalizes unicode, lowercases,
    removes punctuation, and collapses whitespace. The result is a
    lowercase slug that groups drafts for the same logical episode.

    Returns empty string for empty/None titles.
    """
    if not title:
        return ""
    t = re.sub(r"^Episode:\s*", "", title, flags=re.IGNORECASE)
    t = unicodedata.normalize("NFKD", t)
    t = t.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _urls_to_arxiv_set(source_urls_json):
    """Extract arXiv IDs from a JSON-encoded list of URLs."""
    if not source_urls_json:
        return set()
    try:
        urls = json.loads(source_urls_json)
    except (json.JSONDecodeError, TypeError):
        return set()
    ids = set()
    for url in urls:
        m = re.search(r'(\d{4}\.\d{4,5})', url)
        if m:
            ids.add(m.group(1))
    return ids


# ── Revision detection and assignment ──────────────────────────

def find_logical_episode(conn, title, source_urls=None, exclude_id=None):
    """Find existing episodes that share the same logical identity.

    Matches by episode_key (normalized title). If source_urls is
    provided and the title match is ambiguous, arXiv ID overlap is
    used as a tiebreaker.

    Args:
        exclude_id: Optional podcast ID to exclude from results
            (typically the just-inserted row that triggered this
            detection).

    Returns a list of matching podcast rows (as dicts), ordered by
    revision descending (latest first). Empty list if no match.
    """
    key = normalize_episode_key(title)
    if not key:
        return []

    rows = conn.execute(
        "SELECT * FROM podcasts WHERE episode_key = ? "
        "ORDER BY revision DESC, created_at DESC",
        (key,)
    ).fetchall()

    result = [dict(r) for r in rows if r["id"] != exclude_id]
    if result:
        return result

    # Fallback: match by normalized title text even if episode_key
    # was never backfilled (handles pre-migration rows).
    all_rows = conn.execute(
        "SELECT * FROM podcasts ORDER BY created_at DESC"
    ).fetchall()
    matches = []
    for r in all_rows:
        if r["id"] == exclude_id:
            continue
        if normalize_episode_key(r["title"]) == key:
            matches.append(dict(r))
    return matches


def detect_revision(conn, title, source_urls=None, exclude_id=None):
    """Detect whether a new draft is a revision of an existing episode.

    Args:
        exclude_id: Optional podcast ID to exclude from matching
            (the just-inserted row that triggered detection).

    Returns (episode_key, revision_number, superseded_ids) where:
      - episode_key: the normalized grouping key
      - revision_number: the revision to assign (1 if new, N+1 if existing)
      - superseded_ids: list of podcast IDs to mark as superseded
    """
    key = normalize_episode_key(title)
    existing = find_logical_episode(conn, title, source_urls,
                                    exclude_id=exclude_id)

    if not existing:
        return key, 1, []

    max_rev = max(e.get("revision") or 1 for e in existing)
    active_ids = [
        e["id"] for e in existing
        if (e.get("revision_state") or "active") == "active"
    ]
    return key, max_rev + 1, active_ids


def assign_revision(conn, podcast_id, episode_key, revision,
                    superseded_ids=None):
    """Set episode_key and revision on a podcast row, and supersede
    older active revisions for the same logical episode.

    Args:
        conn: SQLite connection.
        podcast_id: The newly inserted podcast ID.
        episode_key: Normalized grouping key.
        revision: Revision number to assign.
        superseded_ids: List of older podcast IDs to mark superseded.
    """
    conn.execute(
        "UPDATE podcasts SET episode_key = ?, revision = ?, "
        "revision_state = 'active' WHERE id = ?",
        (episode_key, revision, podcast_id),
    )
    if superseded_ids:
        for old_id in superseded_ids:
            if old_id != podcast_id:
                conn.execute(
                    "UPDATE podcasts SET revision_state = 'superseded' "
                    "WHERE id = ?",
                    (old_id,),
                )
    conn.commit()


# ── Approval / rejection ───────────────────────────────────────

def approve_revision(conn, podcast_id):
    """Mark a revision as approved and supersede all other active
    revisions for the same logical episode.

    Returns the episode_key, or None if the podcast_id was not found.
    """
    row = conn.execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not row:
        return None

    ep_key = row["episode_key"]
    if not ep_key:
        ep_key = normalize_episode_key(row["title"])

    # Mark this revision as approved
    conn.execute(
        "UPDATE podcasts SET revision_state = 'approved', "
        "episode_key = ? WHERE id = ?",
        (ep_key, podcast_id),
    )

    # Supersede all other revisions for this logical episode
    if ep_key:
        conn.execute(
            "UPDATE podcasts SET revision_state = 'superseded' "
            "WHERE episode_key = ? AND id != ? "
            "AND revision_state = 'active'",
            (ep_key, podcast_id),
        )

    conn.commit()
    return ep_key


def reject_episode_drafts(conn, episode_key):
    """Reject all active draft revisions for a logical episode.

    Only rejects drafts that have not been published. Published
    episodes (those with published_at set) are left untouched.

    Returns the list of rejected podcast IDs.
    """
    if not episode_key:
        return []

    rows = conn.execute(
        "SELECT id, published_at FROM podcasts "
        "WHERE episode_key = ? AND revision_state IN ('active', 'superseded')",
        (episode_key,),
    ).fetchall()

    rejected = []
    for r in rows:
        if r["published_at"]:
            continue
        conn.execute(
            "UPDATE podcasts SET revision_state = 'rejected' WHERE id = ?",
            (r["id"],),
        )
        rejected.append(r["id"])

    conn.commit()
    return rejected


def mark_published(conn, podcast_id):
    """Mark a revision as published. Supersede all other active/approved
    revisions for the same logical episode.

    Returns the episode_key, or None if not found.
    """
    row = conn.execute(
        "SELECT * FROM podcasts WHERE id = ?", (podcast_id,)
    ).fetchone()
    if not row:
        return None

    ep_key = row["episode_key"]
    if not ep_key:
        ep_key = normalize_episode_key(row["title"])

    conn.execute(
        "UPDATE podcasts SET revision_state = 'published', "
        "episode_key = ? WHERE id = ?",
        (ep_key, podcast_id),
    )

    if ep_key:
        conn.execute(
            "UPDATE podcasts SET revision_state = 'superseded' "
            "WHERE episode_key = ? AND id != ? "
            "AND revision_state IN ('active', 'approved')",
            (ep_key, podcast_id),
        )

    conn.commit()
    return ep_key


# ── Stale draft cleanup ───────────────────────────────────────

def find_stale_published_drafts(conn):
    """Find drafts that are already published but still have active
    revision_state. These are leftovers from before the revision
    model was introduced.

    Returns a list of podcast row dicts.
    """
    rows = conn.execute(
        "SELECT * FROM podcasts "
        "WHERE published_at IS NOT NULL AND published_at != '' "
        "AND (revision_state IS NULL OR revision_state = 'active')"
    ).fetchall()
    return [dict(r) for r in rows]


def cleanup_stale_published_drafts(conn):
    """Mark already-published episodes as 'published' in revision_state,
    and backfill their episode_key if missing.

    Returns the number of rows cleaned up.
    """
    stale = find_stale_published_drafts(conn)
    count = 0
    for row in stale:
        ep_key = row.get("episode_key")
        if not ep_key:
            ep_key = normalize_episode_key(row["title"])
        conn.execute(
            "UPDATE podcasts SET revision_state = 'published', "
            "episode_key = ? WHERE id = ?",
            (ep_key, row["id"]),
        )
        count += 1
    conn.commit()
    return count


def backfill_episode_keys(conn):
    """Backfill episode_key for all rows that are missing it.

    Returns the number of rows updated.
    """
    rows = conn.execute(
        "SELECT id, title FROM podcasts "
        "WHERE episode_key IS NULL OR episode_key = ''"
    ).fetchall()
    count = 0
    for r in rows:
        key = normalize_episode_key(r["title"])
        if key:
            conn.execute(
                "UPDATE podcasts SET episode_key = ? WHERE id = ?",
                (key, r["id"]),
            )
            count += 1
    conn.commit()
    return count


# ── Query helpers ──────────────────────────────────────────────

def get_active_drafts(conn):
    """Get all active (non-superseded, non-rejected) draft episodes.

    Returns episodes that have not been published and are in
    'active' state, one per logical episode (latest revision only).
    """
    rows = conn.execute(
        "SELECT * FROM podcasts "
        "WHERE (published_at IS NULL OR published_at = '') "
        "AND revision_state = 'active' "
        "ORDER BY created_at DESC"
    ).fetchall()
    # Deduplicate by episode_key, keeping only the latest
    seen_keys = set()
    result = []
    for r in rows:
        d = dict(r)
        key = d.get("episode_key") or normalize_episode_key(d["title"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        result.append(d)
    return result


def get_revision_history(conn, episode_key):
    """Get all revisions for a logical episode, ordered by revision.

    Returns a list of podcast row dicts.
    """
    if not episode_key:
        return []
    rows = conn.execute(
        "SELECT * FROM podcasts WHERE episode_key = ? "
        "ORDER BY revision ASC, created_at ASC",
        (episode_key,),
    ).fetchall()
    return [dict(r) for r in rows]
