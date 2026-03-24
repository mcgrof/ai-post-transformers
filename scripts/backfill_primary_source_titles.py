#!/usr/bin/env python3
"""Backfill podcast descriptions so the first source line uses title/authors/year."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import get_connection, init_db, update_podcast
from podcast import _format_source_entry, _resolve_source_info


FIRST_SOURCE_RE = re.compile(
    r"(?P<head>.*?Sources:\n)\s*1\.\s*(?P<first>.+?)(?:\n\s{5}(?P<url>https?://\S+))?(?P<tail>\n.*)?$",
    re.S,
)


def _rewrite_description(conn, row):
    description = row["description"] or ""
    if "Sources:" not in description:
        return None

    match = FIRST_SOURCE_RE.match(description)
    if not match:
        return None

    source_urls = []
    if row["source_urls"]:
        try:
            source_urls = json.loads(row["source_urls"])
        except Exception:
            source_urls = []

    url = match.group("url") or (source_urls[0] if source_urls else "")
    if not url:
        return None

    source_info = _resolve_source_info(conn, url, fallback_title=row["title"] or "")
    new_first = "\n".join(
        _format_source_entry(
            1,
            source_info.get("title", url),
            source_info.get("authors", ""),
            source_info.get("year", ""),
            source_info.get("url", url),
        )
    )
    return (match.group("head") + new_first + (match.group("tail") or "")).strip()


def main():
    conn = get_connection()
    init_db(conn)
    rows = conn.execute(
        "SELECT id, title, source_urls, description FROM podcasts WHERE description LIKE '%Sources:%'"
    ).fetchall()
    updated = 0
    for row in rows:
        new_description = _rewrite_description(conn, row)
        if not new_description or new_description == row["description"]:
            continue
        update_podcast(conn, row["id"], description=new_description)
        updated += 1
    conn.commit()
    conn.close()
    print(f"updated {updated} podcast descriptions")


if __name__ == "__main__":
    main()
