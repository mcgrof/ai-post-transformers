#!/usr/bin/env python3
"""Bridge queue approvals to generation submissions.

This script scans queue.yaml/queue.json for papers marked for generation
and creates submission records so the generation worker will process them.

Papers in the queue need a status change to trigger generation.
Currently this is manual, but this script automates the bridge.

Usage:
    python scripts/queue_to_submissions.py [--status BRIDGE|PUBLIC] [--max N]

    --status: Which queue section to convert (default: BRIDGE)
    --max: Maximum papers to submit (default: 5)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status",
        default="bridge",
        choices=["bridge", "public", "memory", "monitor"],
        help="Queue section to convert (default: bridge)",
    )
    parser.add_argument("--max", type=int, default=5, help="Max papers to submit")
    args = parser.parse_args()

    # Load queue.yaml
    import yaml

    queue_path = ROOT / "queue.yaml"
    if not queue_path.exists():
        print("No queue.yaml found. Generate with: python gen-podcast.py queue")
        sys.exit(1)

    with open(queue_path) as f:
        queue = yaml.safe_load(f) or {}

    section = queue.get(args.status, [])
    if not section:
        print(f"Queue section {args.status} is empty")
        return

    # Load existing submissions so we don't duplicate
    import sqlite3

    queue_db = Path.home() / ".local" / "state" / "ai-post-transformers" / "queue.db"
    if not queue_db.exists():
        print(f"Queue database not found: {queue_db}")
        sys.exit(1)

    conn = sqlite3.connect(str(queue_db))
    conn.row_factory = sqlite3.Row
    existing_urls = set()
    try:
        rows = conn.execute(
            "SELECT json_extract(data_json, '$.urls') FROM submissions"
        ).fetchall()
        for row in rows:
            if row[0]:
                try:
                    urls = json.loads(row[0])
                    existing_urls.update(urls if isinstance(urls, list) else [urls])
                except:
                    pass
    except:
        pass
    conn.close()

    # Get papers to submit
    papers_to_submit = []
    for paper in section[: args.max]:
        url = paper.get("arxiv_url", "")
        if url and url not in existing_urls:
            papers_to_submit.append(paper)

    if not papers_to_submit:
        print(f"No new papers to submit from {args.status} section")
        return

    print(
        f"Submitting {len(papers_to_submit)} papers from {args.status} "
        f"section for generation..."
    )

    # Create submissions
    now = datetime.now(timezone.utc)

    for paper in papers_to_submit:
        title = paper.get("title", "?")
        url = paper.get("arxiv_url", "")

        submission_id = str(uuid.uuid4())
        submission_data = {
            "status": "submitted",
            "urls": [url],
            "title": title,
            "source_urls": [url],
            "timestamp": now.isoformat(),
            "updated_at": now.isoformat(),
            "status_history": [
                {"at": now.isoformat(), "status": "submitted"}
            ],
            "metadata": {
                "arxiv_id": paper.get("arxiv_id", ""),
                "authors": paper.get("authors", []),
                "published": paper.get("published", ""),
                "queue_status": args.status,
                "composite_score": paper.get("composite_score", 0),
            },
        }

        # Write to submissions table
        try:
            conn = sqlite3.connect(str(queue_db))
            conn.execute(
                """
                INSERT INTO submissions (key, data_json, status, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    f"submissions/{submission_id}.json",
                    json.dumps(submission_data),
                    "submitted",
                    now.isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            print(f"  ✓ {title}")
        except Exception as e:
            print(f"  ✗ {title}: {e}")

    print(f"\n✓ Submitted {len(papers_to_submit)} papers for generation")
    print("  Run: podcast-worker --once (or wait for next 2-minute timer)")


if __name__ == "__main__":
    main()
