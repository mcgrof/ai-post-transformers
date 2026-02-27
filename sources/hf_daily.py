"""Scrape HuggingFace Daily Papers for trending signal."""

import re
import sys
import requests
from datetime import datetime


HF_DAILY_URL = "https://huggingface.co/papers"


def fetch_hf_daily_papers():
    """Fetch paper IDs from the HuggingFace Daily Papers page.

    Returns a set of arXiv IDs that appear on today's HF daily page.
    """
    print("[HF Daily] Fetching trending papers...", file=sys.stderr)
    try:
        resp = requests.get(HF_DAILY_URL, timeout=30, headers={
            "User-Agent": "paper-feed/1.0 (research tool)"
        })
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[HF Daily] Error fetching page: {e}", file=sys.stderr)
        return set()

    # HF papers page links to /papers/ARXIV_ID
    arxiv_ids = set()
    pattern = re.compile(r'/papers/(\d{4}\.\d{4,5})')
    for match in pattern.finditer(resp.text):
        arxiv_ids.add(match.group(1))

    print(f"[HF Daily] Found {len(arxiv_ids)} trending papers", file=sys.stderr)
    return arxiv_ids


if __name__ == "__main__":
    ids = fetch_hf_daily_papers()
    for aid in sorted(ids):
        print(f"  {aid}")
    print(f"Total: {len(ids)}")
