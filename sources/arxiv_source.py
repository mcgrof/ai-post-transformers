"""Fetch recent papers from arXiv API."""

import arxiv
import sys
import time
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


def fetch_arxiv_papers_extended(categories, days_back=180, max_results=5000):
    """Fetch papers from arXiv over an extended date range (up to 6 months).

    Uses chunked fetching with pagination to handle large result sets.
    Papers are fetched in monthly chunks to stay within arXiv API limits.
    Returns papers with a 'time_window' field indicating which window
    (30d, 90d, 180d) the paper falls into.

    Args:
        categories: List of arXiv category strings (e.g. ['cs.LG', 'cs.AI'])
        days_back: How many days to look back (default 180 = 6 months)
        max_results: Maximum total papers to fetch across all chunks
    """
    papers = []
    seen_ids = set()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Define time window boundaries
    window_30 = now - timedelta(days=30)
    window_90 = now - timedelta(days=90)
    window_180 = now - timedelta(days=days_back)

    cat_query = " OR ".join(f"cat:{c}" for c in categories)

    # Chunk into monthly segments to avoid API timeouts
    chunk_days = 30
    num_chunks = (days_back + chunk_days - 1) // chunk_days
    results_per_chunk = max(200, max_results // num_chunks)

    print(f"[arXiv-ext] Fetching up to {days_back} days "
          f"({num_chunks} chunks, {results_per_chunk}/chunk) "
          f"from {len(categories)} categories...", file=sys.stderr)

    total_fetched = 0
    for chunk_idx in range(num_chunks):
        if total_fetched >= max_results:
            break

        chunk_start = now - timedelta(days=(chunk_idx + 1) * chunk_days)
        chunk_end = now - timedelta(days=chunk_idx * chunk_days)

        # arXiv API query with date range
        # submittedDate format: [YYYYMMDDHHMM TO YYYYMMDDHHMM]
        date_start = chunk_start.strftime("%Y%m%d0000")
        date_end = chunk_end.strftime("%Y%m%d2359")
        date_query = f"submittedDate:[{date_start} TO {date_end}]"
        full_query = f"({cat_query}) AND {date_query}"

        client = arxiv.Client(page_size=200, delay_seconds=3.0, num_retries=3)
        search = arxiv.Search(
            query=full_query,
            max_results=min(results_per_chunk, max_results - total_fetched),
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        chunk_count = 0
        try:
            for result in client.results(search):
                pub_date = result.published.replace(tzinfo=None)

                arxiv_id = result.entry_id.split("/abs/")[-1]
                base_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id

                if base_id in seen_ids:
                    continue
                seen_ids.add(base_id)

                # Determine time window
                if pub_date >= window_30:
                    time_window = "30d"
                elif pub_date >= window_90:
                    time_window = "90d"
                else:
                    time_window = "180d"

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
                    "time_window": time_window,
                })
                chunk_count += 1
                total_fetched += 1

        except Exception as e:
            print(f"[arXiv-ext] Chunk {chunk_idx+1} error: {e}", file=sys.stderr)

        if chunk_count > 0:
            print(f"[arXiv-ext] Chunk {chunk_idx+1}/{num_chunks}: "
                  f"{chunk_count} papers", file=sys.stderr)

        # Be polite to arXiv API between chunks
        if chunk_idx < num_chunks - 1:
            time.sleep(3)

    print(f"[arXiv-ext] Total fetched: {len(papers)} papers "
          f"(30d: {sum(1 for p in papers if p.get('time_window') == '30d')}, "
          f"90d: {sum(1 for p in papers if p.get('time_window') == '90d')}, "
          f"180d: {sum(1 for p in papers if p.get('time_window') == '180d')})",
          file=sys.stderr)
    return papers


if __name__ == "__main__":
    cats = ["cs.LG", "cs.AI", "stat.ML", "cs.CL", "cs.CV", "cs.CR", "cs.OS"]
    # Quick test with recent papers
    papers = fetch_arxiv_papers(cats, hours_back=48)
    for p in papers[:5]:
        print(f"  {p['arxiv_id']}: {p['title'][:80]}")
    print(f"Total (48h): {len(papers)}")

    # Extended test
    print("\nExtended fetch (7 days for testing):")
    papers_ext = fetch_arxiv_papers_extended(cats, days_back=7, max_results=100)
    for p in papers_ext[:5]:
        print(f"  {p['arxiv_id']} [{p.get('time_window', '?')}]: {p['title'][:70]}")
    print(f"Total (7d): {len(papers_ext)}")
