"""Enrich papers with Semantic Scholar citation data."""

import os
import sys
import time
import requests


S2_API_URL = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "citationCount,influentialCitationCount,fieldsOfStudy"


def enrich_papers(papers, batch_size=20):
    """Add citation counts and velocity to papers from Semantic Scholar.

    Uses batch endpoint for efficiency. Respects rate limits.
    """
    api_key = os.environ.get("S2_API_KEY")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    print(f"[S2] Enriching {len(papers)} papers with citation data...", file=sys.stderr)
    enriched = 0

    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        paper_ids = [f"ArXiv:{p['arxiv_id']}" for p in batch]

        try:
            resp = requests.post(
                f"{S2_API_URL}/paper/batch",
                json={"ids": paper_ids},
                params={"fields": S2_FIELDS},
                headers=headers,
                timeout=30,
            )

            if resp.status_code == 429:
                print("[S2] Rate limited, waiting 5s...", file=sys.stderr)
                time.sleep(5)
                resp = requests.post(
                    f"{S2_API_URL}/paper/batch",
                    json={"ids": paper_ids},
                    params={"fields": S2_FIELDS},
                    headers=headers,
                    timeout=30,
                )

            if resp.status_code != 200:
                print(f"[S2] Batch request failed: {resp.status_code}", file=sys.stderr)
                continue

            results = resp.json()
            for paper, s2_data in zip(batch, results):
                if s2_data is None:
                    continue
                paper["citation_count"] = s2_data.get("citationCount", 0) or 0
                influential = s2_data.get("influentialCitationCount", 0) or 0
                # Citation velocity: weighted combo of total + influential
                paper["citation_velocity"] = (
                    paper["citation_count"] * 0.3 + influential * 2.0
                )
                enriched += 1

        except requests.RequestException as e:
            print(f"[S2] Request error: {e}", file=sys.stderr)

        # Rate limit: ~100 req/5 min without key
        if not api_key and i + batch_size < len(papers):
            time.sleep(1)

    print(f"[S2] Enriched {enriched}/{len(papers)} papers", file=sys.stderr)
    return papers


if __name__ == "__main__":
    test_papers = [
        {"arxiv_id": "2302.13971", "title": "LLaMA"},
        {"arxiv_id": "2307.09288", "title": "Llama 2"},
    ]
    enriched = enrich_papers(test_papers)
    for p in enriched:
        print(f"  {p['arxiv_id']}: citations={p.get('citation_count', 0)}, "
              f"velocity={p.get('citation_velocity', 0):.1f}")
