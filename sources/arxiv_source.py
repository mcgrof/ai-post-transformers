"""Fetch recent papers from arXiv API."""

import arxiv
import sys
from datetime import datetime, timedelta, timezone


def fetch_arxiv_papers(categories, hours_back=48):
    """Fetch papers from arXiv in the given categories from the last N hours.

    Uses 48h window by default to handle weekends/delays in arXiv indexing.
    """
    papers = []
    seen_ids = set()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_back)

    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    client = arxiv.Client(page_size=200, delay_seconds=3.0, num_retries=3)
    search = arxiv.Search(
        query=cat_query,
        max_results=500,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    print(f"[arXiv] Fetching papers from {len(categories)} categories...", file=sys.stderr)
    for result in client.results(search):
        pub_date = result.published.replace(tzinfo=None)
        if pub_date < cutoff:
            break

        arxiv_id = result.entry_id.split("/abs/")[-1]
        # Strip version suffix for dedup
        base_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id

        if base_id in seen_ids:
            continue
        seen_ids.add(base_id)

        authors = [a.name for a in result.authors[:10]]
        cats = [c for c in (result.categories or [])]

        papers.append({
            "arxiv_id": base_id,
            "title": result.title.replace("\n", " ").strip(),
            "authors": authors,
            "abstract": result.summary.replace("\n", " ").strip(),
            "categories": cats,
            "arxiv_url": result.entry_id,
            "published": pub_date.isoformat(),
        })

    print(f"[arXiv] Fetched {len(papers)} papers", file=sys.stderr)
    return papers


if __name__ == "__main__":
    cats = ["cs.LG", "cs.AI", "stat.ML", "cs.CL", "cs.CV", "cs.CR", "cs.OS"]
    papers = fetch_arxiv_papers(cats, hours_back=48)
    for p in papers[:5]:
        print(f"  {p['arxiv_id']}: {p['title'][:80]}")
    print(f"Total: {len(papers)}")
