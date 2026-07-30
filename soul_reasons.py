"""SOUL.md opening reason rotation to avoid phrase staleness."""

import yaml
from pathlib import Path
from db import get_connection, init_db


REASONS_PATH = Path(__file__).parent / "SOUL_REASONS.yaml"
DEFAULT_LOOKBACK_EPISODES = 10


def load_reason_pools():
    """Load SOUL character reason pools from YAML."""
    if not REASONS_PATH.exists():
        return {}
    with open(REASONS_PATH) as f:
        data = yaml.safe_load(f) or {}
    return {
        char: {
            "reasons": pool.get("reasons", []),
        }
        for char, pool in data.items()
        if isinstance(pool, dict)
    }


def get_recent_reasons(conn, character, lookback=DEFAULT_LOOKBACK_EPISODES):
    """Query recently used opening reasons for a character.

    Args:
        conn: Database connection
        character: Character name (Hal, Ada, VERA)
        lookback: Number of recent episodes to check

    Returns:
        Set of recently used reason strings
    """
    init_db(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT opening_reason FROM podcasts
        WHERE primary_host = ? AND opening_reason IS NOT NULL
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (character, lookback),
    ).fetchall()
    return {row["opening_reason"] for row in rows if row["opening_reason"]}


def get_reason_last_used(conn, character):
    """Map each used opening reason to the id of its most recent use.

    Higher id == more recently used. Reasons never used are simply
    absent from the map.
    """
    init_db(conn)
    rows = conn.execute(
        """
        SELECT opening_reason, MAX(id) AS last_id FROM podcasts
        WHERE primary_host = ? AND opening_reason IS NOT NULL
        GROUP BY opening_reason
        """,
        (character,),
    ).fetchall()
    return {row["opening_reason"]: row["last_id"] for row in rows}


def select_opening_reason(character, title="", abstract=""):
    """Select the least-recently-used opening reason for a character.

    The old logic returned available_reasons[0] whenever every reason
    had been used at least once — which, with a small pool, meant it got
    permanently stuck on the first reason (Ada delivered "The theoretical
    grounding is what sold me on this one" for ~50 episodes straight).
    Rotate by staleness instead: a never-used reason (sentinel -1) wins
    first, otherwise the reason whose most recent use is oldest; pool
    order breaks ties.

    Args:
        character: Character name (Hal, Ada, VERA)
        title: Paper title (reserved for future semantic ranking)
        abstract: Paper abstract (reserved for future semantic ranking)

    Returns:
        Selected opening reason string, or None if no pool available
    """
    pools = load_reason_pools()
    if character not in pools:
        return None

    available_reasons = pools[character]["reasons"]
    if not available_reasons:
        return None

    try:
        conn = get_connection()
        last_used = get_reason_last_used(conn, character)
        conn.close()
    except Exception:
        last_used = {}

    return min(
        enumerate(available_reasons),
        key=lambda iv: (last_used.get(iv[1], -1), iv[0]),
    )[1]


def track_opening_reason(conn, episode_id, reason, character):
    """Record the opening reason used for an episode.

    Args:
        conn: Database connection
        episode_id: Podcast ID
        reason: The opening reason used
        character: The character who delivered it
    """
    conn.execute(
        """
        UPDATE podcasts
        SET opening_reason = ?, primary_host = ?
        WHERE id = ?
        """,
        (reason, character, episode_id),
    )
    conn.commit()
