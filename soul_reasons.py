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


def select_opening_reason(character, title="", abstract=""):
    """Select an opening reason for a character, avoiding recent repetition.

    Args:
        character: Character name (Hal, Ada, VERA)
        title: Paper title (for semantic matching, optional)
        abstract: Paper abstract (for semantic matching, optional)

    Returns:
        Selected opening reason string, or None if no pool available
    """
    pools = load_reason_pools()
    if character not in pools:
        return None

    available_reasons = pools[character]["reasons"]
    if not available_reasons:
        return None

    # Get recently used reasons
    try:
        conn = get_connection()
        recent = get_recent_reasons(conn, character)
        conn.close()
    except Exception:
        recent = set()

    # Filter out recently used
    fresh_reasons = [r for r in available_reasons if r not in recent]

    # If all reasons were recently used, cycle back to the oldest (full rotation)
    if not fresh_reasons:
        fresh_reasons = available_reasons

    # For now, pick the first available (TODO: semantic ranking by paper content)
    return fresh_reasons[0] if fresh_reasons else None


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
