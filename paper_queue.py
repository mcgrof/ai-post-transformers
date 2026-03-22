"""Paper queue: discover, score, and rank papers awaiting podcast coverage.

Produces queue.yaml (machine-readable), podcasts/queue.xml (RSS),
and podcasts/queue.html (static page) so the backlog is visible
and subscribable.
"""

import glob
import html
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from db import get_connection, init_db, get_all_episode_arxiv_ids, get_covered_topics
from sources.arxiv_source import fetch_arxiv_papers, fetch_arxiv_papers_extended
from sources.hf_daily import fetch_hf_daily_papers
from sources.semantic import enrich_papers
from sources.social_signals import fetch_social_signals


# --- Colored terminal output ---

def _isatty():
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

_COLORS = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "cyan":    "\033[36m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "magenta": "\033[35m",
    "red":     "\033[31m",
    "blue":    "\033[34m",
}

def _c(color, text):
    if not _isatty():
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"

def _log(tag, msg, color="cyan"):
    print(f"{_c(color, tag)} {msg}", file=sys.stderr)


def run_queue(config):
    """Main entry point: fetch, score, dedup, and write queue outputs."""
    t0 = time.monotonic()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    editorial_enabled = config.get("editorial", {}).get("enabled", False)

    # Init DB, collect all arXiv IDs already covered
    conn = get_connection()
    init_db(conn)
    episode_ids = get_all_episode_arxiv_ids(conn)
    covered_topics = get_covered_topics(conn)
    conn.close()

    draft_ids = _get_draft_arxiv_ids()
    exclude_ids = episode_ids | draft_ids
    _log("[Queue]", f"Excluding {_c('bold', str(len(exclude_ids)))} "
         "already-covered papers")

    categories = config.get("arxiv_categories", [])
    github_repo = config.get("github", {}).get("repo", "")

    # Determine fetch mode: extended (6 months) or standard (48h)
    fetch_mode = config.get("queue", {}).get("fetch_mode", "standard")
    days_back = config.get("queue", {}).get("days_back", 180)
    max_arxiv_results = config.get("queue", {}).get("max_arxiv_results", 5000)

    # --- Parallel fetch: arXiv + HF Daily + GitHub Issues + model preload ---
    _log("[Queue]", f"Fetching sources in parallel "
         f"(mode={_c('bold', fetch_mode)})...", "magenta")
    with ThreadPoolExecutor(max_workers=5) as pool:
        if fetch_mode == "extended":
            fut_arxiv = pool.submit(
                fetch_arxiv_papers_extended, categories,
                days_back=days_back, max_results=max_arxiv_results)
        else:
            fut_arxiv = pool.submit(fetch_arxiv_papers, categories)
        fut_hf = pool.submit(fetch_hf_daily_papers)
        fut_gh = pool.submit(
            fetch_github_issues, github_repo) if github_repo else None
        if editorial_enabled:
            fut_scorer = pool.submit(
                _preload_editorial_scorer, config,
                episode_ids, covered_topics)
        else:
            fut_scorer = pool.submit(
                _preload_scorer, config, episode_ids)

        papers = fut_arxiv.result()
        hf_ids = fut_hf.result()
        gh_papers = fut_gh.result() if fut_gh else []
        scorer = fut_scorer.result()

    if not papers:
        _log("[Queue]", "No papers fetched from arXiv.", "red")
        sys.exit(1)

    # Apply HF trending signal
    for p in papers:
        if p["arxiv_id"] in hf_ids:
            p["hf_daily"] = True

    # Semantic Scholar enrichment (sequential, rate-limited)
    papers = enrich_papers(papers)

    # Social/influencer signals
    social_signals = {}
    if config.get("social_signals", {}).get("enabled", True):
        social_signals = fetch_social_signals(papers, config)
        _log("[Queue]", f"Social signals: {_c('bold', str(len(social_signals)))} "
             f"papers with signals")

    # Merge GitHub Issues submissions
    if gh_papers:
        seen = {p["arxiv_id"] for p in papers}
        added = 0
        for gp in gh_papers:
            if gp["arxiv_id"] not in seen:
                papers.append(gp)
                seen.add(gp["arxiv_id"])
                added += 1
        if added:
            _log("[Queue]", f"Added {_c('green', str(added))} papers "
                 "from GitHub Issues")

    # Remove already-covered papers
    before = len(papers)
    papers = [p for p in papers if p["arxiv_id"] not in exclude_ids]
    _log("[Queue]", f"{_c('bold', str(len(papers)))} candidates "
         f"({before - len(papers)} excluded)")

    if not papers:
        _log("[Queue]", "No new papers to queue.", "yellow")
        return

    base = Path(__file__).parent

    if editorial_enabled:
        _run_editorial_queue(scorer, papers, config, date_str, base,
                             social_signals=social_signals)
    else:
        _run_legacy_queue(scorer, papers, config, date_str, base)

    elapsed = time.monotonic() - t0
    _log("[Queue]",
         f"Done in {_c('bold', f'{elapsed:.1f}s')}", "green")


def _run_legacy_queue(scorer, papers, config, date_str, base):
    """Original single-score pipeline (unchanged)."""
    papers = scorer.score_papers(papers)
    top_n = config.get("queue", {}).get("top_n", 30)
    top_papers = papers[:top_n]

    for p in top_papers:
        p.setdefault("added", date_str)
        p.setdefault("source", "digest")

    _write_queue_yaml(top_papers, base / "queue.yaml")
    generate_queue_feed(top_papers, config)
    generate_queue_html(top_papers, config)
    _log("[Queue]",
         f"{_c('green', str(len(top_papers)))} papers queued (legacy)")


def _run_editorial_queue(scorer, papers, config, date_str, base,
                         social_signals=None):
    """Two-pass editorial pipeline with dual lenses."""
    from llm_reviewer import LLMReviewer

    # First pass: editorial scoring (with social signals)
    records = scorer.score_papers(papers, social_signals=social_signals)
    shortlist = scorer.select_shortlist(records)

    # Second pass: LLM review on shortlist
    reviewer = LLMReviewer(config)
    reviewed = reviewer.review_papers(shortlist)

    # Build final queue sections
    sections = build_final_queue(reviewed, records, config)

    # Log section breakdown
    _log("[Queue]",
         f"Editorial sections: "
         f"{_c('green', 'Bridge')} {len(sections.get('bridge', []))}, "
         f"{_c('blue', 'Public')} {len(sections.get('public', []))}, "
         f"{_c('magenta', 'Memory')} {len(sections.get('memory', []))}, "
         f"{_c('dim', 'Monitor')} {len(sections.get('monitor', []))}, "
         f"{_c('yellow', 'Deferred')} "
         f"{len(sections.get('deferred', []))}, "
         f"{_c('red', 'Out of scope')} "
         f"{len(sections.get('out_of_scope', []))}")

    # Write outputs
    write_queue_json(sections, records, base / "queue.json")
    _write_queue_yaml_v2(sections, base / "queue.yaml")
    generate_queue_feed_v2(sections, config)
    generate_queue_html_v2(sections, config)

    total = sum(len(v) for v in sections.values())
    _log("[Queue]",
         f"{_c('green', str(total))} papers queued (editorial)")


def _preload_scorer(config, episode_ids):
    """Load InterestScorer in a background thread while I/O runs."""
    from interests import InterestScorer
    return InterestScorer(config, podcasted_ids=episode_ids)


def _preload_editorial_scorer(config, episode_ids, covered_topics):
    """Load EditorialScorer in a background thread while I/O runs."""
    from editorial_scorer import EditorialScorer
    return EditorialScorer(
        config, podcasted_ids=episode_ids,
        covered_topic_texts=covered_topics if covered_topics else None)


def build_final_queue(reviewed, all_records, config):
    """Partition reviewed papers into Bridge/Public/Memory/Monitor.

    Returns a dict with keys: bridge, public, memory, monitor.
    """
    fq = config.get("editorial", {}).get("final_queue", None)
    if fq is None:
        with open(Path(__file__).parent / "weights.yaml") as f:
            fq = yaml.safe_load(f).get("final_queue", {})

    n_bridge = fq.get("bridge", 10)
    n_public = fq.get("public", 10)
    n_memory = fq.get("memory", 10)
    diversity_cap = fq.get("diversity_cap", 3)
    bridge_min = fq.get("bridge_min_score", 0.30)
    public_min = fq.get("public_min_score", 0.35)
    memory_min = fq.get("memory_min_score", 0.25)
    memory_first_score = fq.get("memory_first_score", 0.40)

    cover_now = [r for r in reviewed if r.status == "Cover now"]
    monitor = [r for r in reviewed if r.status == "Monitor"]
    deferred = [r for r in reviewed
                if r.status == "Deferred this cycle"]
    out_of_scope = [r for r in reviewed
                    if r.status == "Out of scope"]

    # Sort deferred/out-of-scope by highest axis score (most
    # interesting deferrals first for auditing)
    deferred.sort(key=lambda r: r.max_axis_score, reverse=True)
    out_of_scope.sort(
        key=lambda r: r.max_axis_score, reverse=True)

    # Partition Cover now into Bridge, Public-first, Memory-first
    bridge = []
    public_first = []
    memory_first = []

    for r in cover_now:
        if _is_bridge_candidate(r, bridge_min):
            bridge.append(r)
        elif _is_memory_candidate(r, memory_min):
            _add_memory_bucket_label(
                r, memory_first_score=memory_first_score)
            memory_first.append(r)
        else:
            public_first.append(r)

    # Sort each bucket by quality
    bridge.sort(key=lambda r: r.bridge_score, reverse=True)
    public_first.sort(
        key=lambda r: r.public_interest_score, reverse=True)
    memory_first.sort(
        key=lambda r: r.memory_score, reverse=True)
    monitor.sort(
        key=lambda r: r.quality_score * r.teachability,
        reverse=True)

    # Apply diversity cap and take N from each bucket
    bridge = _apply_diversity_cap(bridge, diversity_cap)[:n_bridge]
    public_first = _apply_diversity_cap(
        public_first, diversity_cap)[:n_public]
    memory_first = _apply_diversity_cap(
        memory_first, diversity_cap)[:n_memory]

    # Backfill sparse buckets from Monitor, but keep category integrity.
    for bucket, target, pred, ranker in [
            (bridge, n_bridge,
             lambda r: _is_bridge_candidate(r, bridge_min),
             lambda r: r.bridge_score + 0.2 * r.quality_score),
            (public_first, n_public,
             lambda r: r.public_interest_score >= public_min,
             lambda r: r.public_interest_score + 0.2 * r.quality_score),
            (memory_first, n_memory,
             lambda r: _is_memory_candidate(r, memory_min),
             lambda r: (
                 r.memory_score + 0.2 * r.quality_score
                 + 0.1 * r.bandwidth_capacity
             ))]:
        for cand in sorted(monitor, key=ranker, reverse=True):
            if len(bucket) >= target:
                break
            if not pred(cand):
                continue
            if cand in bucket:
                continue
            if bucket is memory_first:
                _add_memory_bucket_label(
                    cand, memory_first_score=memory_first_score)
            bucket.append(cand)
            monitor.remove(cand)

    # Remaining reviewed papers that weren't placed go to monitor,
    # but skip those already in deferred or out_of_scope
    placed_ids = set()
    for bucket in (bridge, public_first, memory_first):
        for r in bucket:
            placed_ids.add(r.arxiv_id)
    deferred_ids = {r.arxiv_id for r in deferred}
    oos_ids = {r.arxiv_id for r in out_of_scope}
    for r in reviewed:
        if (r.arxiv_id not in placed_ids
                and r not in monitor
                and r.arxiv_id not in deferred_ids
                and r.arxiv_id not in oos_ids):
            monitor.append(r)

    return {
        "bridge": bridge,
        "public": public_first,
        "memory": memory_first,
        "monitor": monitor[:20],
        "deferred": deferred,
        "out_of_scope": out_of_scope,
    }


def _is_bridge_candidate(rec, bridge_min):
    return (
        "Bridge" in rec.badges
        or (rec.public_interest_score >= bridge_min
            and rec.memory_score >= bridge_min)
    )


def _is_memory_candidate(rec, memory_min):
    return (
        "Memory/Storage Core" in rec.badges
        or "Memory/Storage Adjacent" in rec.badges
        or rec.memory_score >= memory_min
    )


def _add_memory_bucket_label(rec, memory_first_score):
    label = "Memory-first" if (
        "Memory/Storage Core" in rec.badges
        or rec.memory_score >= memory_first_score
        or rec.memory_score >= rec.public_interest_score
    ) else "Memory-relevant"
    if label not in rec.badges:
        rec.badges.append(label)


def _apply_diversity_cap(records, cap):
    """Limit very similar papers within a bucket.

    Uses a simple title-based clustering: if more than `cap`
    papers share a common 3-gram in their title, trim extras.
    """
    if cap <= 0 or not records:
        return records

    result = []
    cluster_counts = {}
    for r in records:
        # Simple cluster key: first significant bigram
        words = r.title.lower().split()[:6]
        key = " ".join(words[:3]) if len(words) >= 3 else r.title.lower()
        cluster_counts[key] = cluster_counts.get(key, 0) + 1
        if cluster_counts[key] <= cap:
            result.append(r)
    return result


def write_queue_json(sections, all_records, path):
    """Write full PaperRecord data as JSON for diffing."""
    data = {}
    for section_name, records in sections.items():
        data[section_name] = [r.to_dict() for r in records]

    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    _log("[Queue]", f"JSON  -> {_c('dim', str(path))}", "blue")


def _write_queue_yaml_v2(sections, path):
    """Write sectioned queue as YAML."""
    data = {}
    for section_name, records in sections.items():
        entries = []
        for r in records:
            authors = r.authors
            if len(authors) > 3:
                authors_str = ", ".join(authors[:3]) + " et al."
            else:
                authors_str = ", ".join(authors)
            entries.append({
                "arxiv_id": r.arxiv_id,
                "title": r.title,
                "authors": authors_str,
                "arxiv_url": r.url,
                "published": r.published_at,
                "public_interest_score": round(
                    r.public_interest_score, 4),
                "memory_score": round(r.memory_score, 4),
                "status": r.status,
                "badges": r.badges,
            })
        data[section_name] = entries

    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)
    total = sum(len(v) for v in data.values())
    _log("[Queue]", f"YAML  -> {_c('dim', str(path))} "
         f"({total} papers)", "blue")


def fetch_github_issues(repo):
    """Fetch paper submissions from GitHub Issues labeled 'paper-submission'.

    Uses the gh CLI. Returns a list of paper dicts with arXiv metadata
    fetched from the arXiv API. Gracefully returns [] on any failure.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"/repos/{repo}/issues",
             "--jq", ".",
             "-q", "label:paper-submission",
             "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            _log("[Queue]", f"gh CLI failed: {result.stderr.strip()}",
                 "yellow")
            return []
        issues = json.loads(result.stdout) if result.stdout.strip() else []
    except FileNotFoundError:
        _log("[Queue]", "gh CLI not found, skipping GitHub Issues", "dim")
        return []
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        _log("[Queue]", f"GitHub Issues error: {e}", "yellow")
        return []

    # Filter to issues with the paper-submission label
    papers = []
    arxiv_re = re.compile(r'(\d{4}\.\d{4,5})')
    for issue in issues:
        labels = [lb.get("name", "") for lb in issue.get("labels", [])]
        if "paper-submission" not in labels:
            continue
        body = issue.get("body", "") or ""
        m = arxiv_re.search(body)
        if not m:
            continue
        arxiv_id = m.group(1)
        meta = _fetch_arxiv_meta(arxiv_id)
        if meta:
            meta["source"] = "github-issue"
            meta["issue_number"] = issue.get("number")
            papers.append(meta)

    return papers


def _fetch_arxiv_meta(arxiv_id):
    """Fetch title, authors, abstract from the arXiv API for a single paper."""
    import urllib.request
    import xml.etree.ElementTree as ET

    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read()
    except Exception as e:
        _log("[Queue]", f"arXiv API error for {arxiv_id}: {e}", "yellow")
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(data)
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    title_el = entry.find("atom:title", ns)
    abstract_el = entry.find("atom:summary", ns)
    authors = [
        a.find("atom:name", ns).text
        for a in entry.findall("atom:author", ns)
        if a.find("atom:name", ns) is not None
    ]
    published_el = entry.find("atom:published", ns)

    return {
        "arxiv_id": arxiv_id,
        "title": title_el.text.replace("\n", " ").strip() if title_el is not None else "",
        "authors": authors,
        "abstract": abstract_el.text.replace("\n", " ").strip() if abstract_el is not None else "",
        "categories": [],
        "arxiv_url": f"http://arxiv.org/abs/{arxiv_id}",
        "published": published_el.text if published_el is not None else "",
    }


def _get_draft_arxiv_ids():
    """Scan drafts/**/*.json for source_urls to find arXiv IDs in progress."""
    ids = set()
    arxiv_re = re.compile(r'(\d{4}\.\d{4,5})')
    for json_path in glob.glob("drafts/**/*.json", recursive=True):
        try:
            with open(json_path) as f:
                data = json.load(f)
            for url in data.get("source_urls", []):
                m = arxiv_re.search(url)
                if m:
                    ids.add(m.group(1))
        except (json.JSONDecodeError, OSError):
            pass
    return ids


def _write_queue_yaml(papers, path):
    """Write scored papers as a YAML list."""
    entries = []
    for p in papers:
        authors = p.get("authors", [])
        if len(authors) > 3:
            authors_str = ", ".join(authors[:3]) + " et al."
        else:
            authors_str = ", ".join(authors)
        entries.append({
            "arxiv_id": p["arxiv_id"],
            "title": p["title"],
            "authors": authors_str,
            "arxiv_url": p.get("arxiv_url", f"http://arxiv.org/abs/{p['arxiv_id']}"),
            "published": p.get("published", ""),
            "score": p.get("score", 0.0),
            "score_reason": p.get("score_reason", ""),
            "source": p.get("source", "digest"),
            "added": p.get("added", ""),
        })

    with open(path, "w") as f:
        yaml.dump(entries, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False)
    _log("[Queue]", f"YAML  -> {_c('dim', str(path))} "
         f"({len(entries)} papers)", "blue")


def generate_queue_feed(papers, config):
    """Generate RSS 2.0 feed at podcasts/queue.xml."""
    show = config.get("spotify", {}).get("show", {})
    show_title = show.get("title", "AI Post Transformers")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{show_title} \u2014 Paper Queue"
    ET.SubElement(channel, "description").text = (
        "Ranked list of AI research papers under consideration for "
        f"upcoming {show_title} episodes."
    )
    ET.SubElement(channel, "link").text = show.get(
        "link", "https://podcast.do-not-panic.com")
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc))

    for p in papers:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = p.get("title", "")
        arxiv_url = p.get("arxiv_url", f"http://arxiv.org/abs/{p['arxiv_id']}")
        ET.SubElement(item, "link").text = arxiv_url

        abstract = p.get("abstract", "")
        if len(abstract) > 500:
            abstract = abstract[:497] + "..."
        score = p.get("score", 0.0)
        ET.SubElement(item, "description").text = (
            f"{abstract}\n\nInterest score: {score:.3f}"
        )

        published = p.get("published", "")
        if published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ET.SubElement(item, "pubDate").text = format_datetime(dt)
            except (ValueError, TypeError):
                pass

        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = p["arxiv_id"]

    feed_path = Path(__file__).parent / "podcasts" / "queue.xml"
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(str(feed_path), xml_declaration=True, encoding="UTF-8")
    _log("[Queue]", f"RSS   -> {_c('dim', str(feed_path))} "
         f"({len(papers)} items)", "blue")
    return str(feed_path)


_REASON_COLORS = [
    ("matches '",        "tip-similarity"),
    ("secondary interest", "tip-similarity"),
    ("keywords:",        "tip-keyword"),
    ("HF trending",      "tip-hf"),
    ("cited",            "tip-citation"),
    ("already podcasted", "tip-penalty"),
]


def _color_reason_line(reason):
    """Wrap a score_reason part in a colored tip-line div."""
    css_class = ""
    lower = reason.lower()
    for prefix, cls in _REASON_COLORS:
        if prefix.lower() in lower:
            css_class = cls
            break
    escaped = html.escape(reason)
    if css_class:
        return f'<div class="tip-line {css_class}">{escaped}</div>'
    return f'<div class="tip-line">{escaped}</div>'


def generate_queue_html(papers, config):
    """Generate a static HTML page at podcasts/queue.html."""
    show = config.get("spotify", {}).get("show", {})
    show_title = show.get("title", "AI Post Transformers")
    github_repo = config.get("github", {}).get("repo", "")

    rows_html = []
    for i, p in enumerate(papers, 1):
        authors = p.get("authors", [])
        if isinstance(authors, list):
            if len(authors) > 3:
                authors_str = ", ".join(authors[:3]) + " et al."
            else:
                authors_str = ", ".join(authors)
        else:
            authors_str = str(authors)

        arxiv_url = p.get("arxiv_url", f"http://arxiv.org/abs/{p['arxiv_id']}")
        published = p.get("published", "")
        if published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                published = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        score = p.get("score", 0.0)
        reason = html.escape(p.get("score_reason", ""))
        source = p.get("source", "digest")
        source_badge = ""
        if source == "github-issue":
            issue_num = p.get("issue_number", "")
            source_badge = f' <span class="badge">#{issue_num}</span>'

        # Break score_reason into labeled parts for the tooltip
        reason_parts = p.get("score_reason", "").split("; ")
        reason_lines = "".join(
            _color_reason_line(r) for r in reason_parts if r
        )

        rows_html.append(
            f'<tr>'
            f'<td class="rank">{i}</td>'
            f'<td><a href="{html.escape(arxiv_url)}" target="_blank">'
            f'{html.escape(p.get("title", ""))}</a>{source_badge}</td>'
            f'<td class="authors">{html.escape(authors_str)}</td>'
            f'<td class="date">{html.escape(published)}</td>'
            f'<td class="score">{score:.3f}'
            f'<div class="score-tip">'
            f'<div class="tip-header">Score breakdown</div>'
            f'{reason_lines}</div></td>'
            f'</tr>'
        )

    table_rows = "\n".join(rows_html)
    count = len(papers)
    n_categories = len(config.get("arxiv_categories", []))
    top_n = config.get("queue", {}).get("top_n", 30)

    # GitHub Issues JS block (only included when repo is configured)
    gh_js = ""
    if github_repo:
        gh_js = f"""
<script>
(function() {{
  var repo = "{html.escape(github_repo)}";
  var url = "https://api.github.com/repos/" + repo +
            "/issues?labels=paper-submission&state=open&per_page=20";
  fetch(url)
    .then(function(r) {{ return r.json(); }})
    .then(function(issues) {{
      if (!issues.length) return;
      var sec = document.getElementById("gh-submissions");
      sec.style.display = "block";
      var list = sec.querySelector("ul");
      issues.forEach(function(iss) {{
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = iss.html_url;
        a.target = "_blank";
        a.textContent = iss.title;
        li.appendChild(a);
        list.appendChild(li);
      }});
    }})
    .catch(function() {{}});
}})();
</script>"""

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Queue &mdash; {html.escape(show_title)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
  background: url("images/queue-bg.png") center center / cover no-repeat fixed,
              #141414;
  color: #e5e5e5;
  line-height: 1.5;
  min-height: 100vh;
}}
body::before {{
  content: "";
  position: fixed;
  inset: 0;
  background: rgba(14, 14, 14, 0.82);
  z-index: 0;
  pointer-events: none;
}}
body > * {{
  position: relative;
  z-index: 1;
}}
a {{ color: #ccc; text-decoration: none; }}
a:hover {{ color: #fff; text-decoration: underline; }}
.hero {{
  position: relative;
  padding: 3rem 2rem 2rem;
  text-align: center;
  background: transparent;
  overflow: visible;
  z-index: 10;
}}
.hero::before {{
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at 50% 0%, rgba(229,9,20,0.12) 0%, transparent 70%);
  pointer-events: none;
}}
.hero h1 {{
  font-size: 2rem;
  font-weight: 700;
  color: #fff;
  margin-bottom: 0.4rem;
  position: relative;
}}
.hero p {{
  max-width: 580px;
  margin: 0 auto 1rem;
  color: #999;
  font-size: 0.95rem;
  position: relative;
}}
.hero-links {{
  display: flex;
  gap: 0.75rem;
  justify-content: center;
  flex-wrap: wrap;
  position: relative;
}}
.hero-links a {{
  padding: 0.5rem 1.4rem;
  border-radius: 4px;
  font-size: 0.9rem;
  font-weight: 600;
  transition: background 0.2s;
}}
.btn-primary {{
  background: #333;
  color: #e5e5e5;
}}
.btn-primary:hover {{ background: #444; text-decoration: none; }}
.btn-outline {{
  border: 1px solid #555;
  color: #e5e5e5;
}}
.btn-outline:hover {{ border-color: #e5e5e5; text-decoration: none; }}
.section {{
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}}
th {{
  text-align: left;
  padding: 0.6rem 0.8rem;
  border-bottom: 2px solid #333;
  color: #999;
  font-weight: 600;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}
td {{
  padding: 0.55rem 0.8rem;
  border-bottom: 1px solid #222;
  vertical-align: top;
}}
tr:hover {{ background: #1c1c1c; }}
.rank {{ width: 3rem; text-align: center; color: #555; }}
.authors {{ color: #888; font-size: 0.82rem; max-width: 220px; }}
.date {{ white-space: nowrap; color: #666; font-size: 0.82rem; }}
.score-header {{
  position: relative;
  text-align: right;
  cursor: help;
}}
.score-header:hover .score-tip {{
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
  pointer-events: auto;
}}
.score-header .score-tip {{
  text-transform: none;
  letter-spacing: normal;
  font-weight: normal;
}}
.tip-line strong.tip-similarity {{ color: #8ab4f8; }}
.tip-line strong.tip-keyword {{ color: #c9a0dc; }}
.tip-line strong.tip-hf {{ color: #f0b27a; }}
.tip-line strong.tip-citation {{ color: #7dcea0; }}
.tip-line strong.tip-penalty {{ color: #e57373; }}
.tip-line.tip-similarity {{ color: #8ab4f8; }}
.tip-line.tip-keyword {{ color: #c9a0dc; }}
.tip-line.tip-hf {{ color: #f0b27a; }}
.tip-line.tip-citation {{ color: #7dcea0; }}
.tip-line.tip-penalty {{ color: #e57373; }}
.score {{
  position: relative;
  text-align: right;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.82rem;
  color: #999;
  cursor: help;
}}
.score-tip {{
  position: absolute;
  right: 0;
  top: 100%;
  width: 280px;
  background: #1c1c1c;
  border-radius: 6px;
  padding: 0.8rem 1rem;
  box-shadow: 0 10px 30px rgba(0,0,0,0.6);
  z-index: 20;
  opacity: 0;
  visibility: hidden;
  transform: translateY(-6px);
  transition: opacity 0.2s ease, visibility 0.2s ease,
              transform 0.2s cubic-bezier(.25,.46,.45,.94);
  pointer-events: none;
  text-align: left;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
}}
.score:hover .score-tip {{
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
  pointer-events: auto;
}}
.tip-header {{
  font-size: 0.75rem;
  font-weight: 600;
  color: #888;
  margin-bottom: 0.4rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid #2a2a2a;
}}
.tip-line {{
  font-size: 0.8rem;
  color: #bbb;
  padding: 0.15rem 0;
  line-height: 1.4;
}}
.badge {{
  display: inline-block;
  background: #252525;
  color: #888;
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  margin-left: 0.4rem;
  vertical-align: middle;
}}
#gh-submissions {{
  display: none;
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid #333;
}}
#gh-submissions h2 {{
  font-size: 1.1rem;
  color: #fff;
  margin-bottom: 0.8rem;
}}
#gh-submissions ul {{
  list-style: none;
  padding: 0;
}}
#gh-submissions li {{
  padding: 0.3rem 0;
  font-size: 0.88rem;
}}
.footer {{
  text-align: center;
  padding: 2rem;
  color: #555;
  font-size: 0.78rem;
  border-top: 1px solid #222;
  margin-top: 2rem;
}}
/* --- Pipeline tooltip on title --- */
.subtitle-wrap {{
  position: relative;
  display: inline-block;
}}
.subtitle {{
  max-width: 580px;
  margin: 0 auto 1rem;
  color: #999;
  font-size: 0.95rem;
  position: relative;
  cursor: help;
}}
.how-link {{
  font-size: 0.95rem;
  font-weight: 600;
  color: #f0b27a;
  border-bottom: 1px dotted #f0b27a;
  margin-left: 0.4rem;
  cursor: help;
}}
.subtitle-wrap:hover .how-link {{ color: #f8c88a; }}
.pipeline-tip {{
  position: absolute;
  top: 100%;
  left: 50%;
  width: 540px;
  max-width: 92vw;
  background: #1c1c1c;
  border-radius: 6px;
  padding: 0.8rem 1rem;
  box-shadow: 0 10px 30px rgba(0,0,0,0.6);
  z-index: 30;
  opacity: 0;
  visibility: hidden;
  transform: translate(-50%, -6px);
  transition: opacity 0.2s ease, visibility 0.2s ease,
              transform 0.2s cubic-bezier(.25,.46,.45,.94);
  pointer-events: none;
  text-align: left;
}}
.subtitle-wrap:hover .pipeline-tip {{
  opacity: 1;
  visibility: visible;
  transform: translate(-50%, 4px);
  pointer-events: auto;
}}
.pip-step {{
  font-size: 0.8rem;
  color: #bbb;
  padding: 0.2rem 0;
  line-height: 1.4;
}}
.pip-num {{
  display: inline-block;
  width: 1.3rem;
  height: 1.3rem;
  line-height: 1.3rem;
  text-align: center;
  font-size: 0.68rem;
  font-weight: 600;
  color: #999;
  background: #252525;
  border-radius: 50%;
  margin-right: 0.4rem;
  vertical-align: middle;
}}
.pip-color {{
  font-weight: 600;
}}

@media (max-width: 700px) {{
  .hero {{ padding: 2rem 1rem 1.5rem; }}
  .hero h1 {{ font-size: 1.5rem; }}
  .authors {{ display: none; }}
  td, th {{ padding: 0.4rem 0.5rem; }}
}}
</style>
</head>
<body>

<div class="hero">
  <h1>Paper Queue</h1>
  <div class="subtitle-wrap">
    <p class="subtitle">{count} papers ranked by interest score
      <span class="how-link">&mdash; scoring algorithm</span>
    </p>
    <div class="pipeline-tip">
      <div class="tip-header">How this queue is built</div>
      <div class="pip-step"><span class="pip-num">1</span>
        <span class="pip-color" style="color:#8ab4f8">Fetch papers</span>
        &mdash; pull recent submissions from arXiv across
        {n_categories} categories.</div>
      <div class="pip-step"><span class="pip-num">2</span>
        <span class="pip-color" style="color:#f0b27a">HF trending</span>
        &mdash; check HuggingFace Daily Papers for trending signal.</div>
      <div class="pip-step"><span class="pip-num">3</span>
        <span class="pip-color" style="color:#7dcea0">Citation data</span>
        &mdash; enrich with Semantic Scholar citation counts
        and influential citations.</div>
      <div class="pip-step"><span class="pip-num">4</span>
        <span class="pip-color" style="color:#c9a0dc">GitHub Issues</span>
        &mdash; merge paper submissions from GitHub Issues
        labeled <em>paper-submission</em>.</div>
      <div class="pip-step"><span class="pip-num">5</span>
        <span class="pip-color" style="color:#e5e5e5">Dedup</span>
        &mdash; remove papers already covered in episodes
        or in-progress drafts.</div>
      <div class="pip-step"><span class="pip-num">6</span>
        <span class="pip-color" style="color:#8ab4f8">Score</span>
        &mdash; embed each paper with all-MiniLM-L6-v2 and rank
        by cosine similarity against research interest profile,
        plus keyword, trending, and citation boosts.</div>
      <div class="pip-step"><span class="pip-num">7</span>
        <span class="pip-color" style="color:#e57373">Top N</span>
        &mdash; take the top {top_n}
        and publish this page.</div>
    </div>
  </div>
  <div class="hero-links">
    <a class="btn-primary" href="queue.xml">RSS Feed</a>
    <a class="btn-outline" href="index.html">&larr; Episodes</a>
  </div>
</div>

<div class="section">
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Title</th>
        <th>Authors</th>
        <th>Published</th>
        <th class="score-header">Score
          <div class="score-tip">
            <div class="tip-header">How scores are computed</div>
            <div class="tip-line"><strong class="tip-similarity">Similarity</strong> &mdash;
              cosine similarity between paper embedding
              (all-MiniLM-L6-v2) and research interest profile.</div>
            <div class="tip-line"><strong class="tip-keyword">Keyword boost</strong> &mdash;
              bonus for matching high/medium/low priority keywords
              in the title and abstract.</div>
            <div class="tip-line"><strong class="tip-hf">HF trending</strong> &mdash;
              papers on the HuggingFace Daily Papers page get
              a +0.15 boost.</div>
            <div class="tip-line"><strong class="tip-citation">Citation velocity</strong> &mdash;
              Semantic Scholar citation count and influential
              citations add up to +0.10.</div>
            <div class="tip-line"><strong class="tip-penalty">Podcast penalty</strong> &mdash;
              papers already covered in an episode are penalized
              by 90%.</div>
          </div>
        </th>
      </tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

  <div id="gh-submissions">
    <h2>Pending Submissions</h2>
    <ul></ul>
  </div>
</div>

<div class="footer">
  {html.escape(show_title)} &mdash; Paper Queue
</div>

{gh_js}
</body>
</html>"""

    html_path = Path(__file__).parent / "podcasts" / "queue.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page)
    _log("[Queue]", f"HTML  -> {_c('dim', str(html_path))} "
         f"({count} papers)", "blue")
    return str(html_path)


# ---------------------------------------------------------------
# Editorial v2 output generators
# ---------------------------------------------------------------

def generate_queue_feed_v2(sections, config):
    """Generate RSS 2.0 feed with section info, badges, dual scores."""
    show = config.get("spotify", {}).get("show", {})
    show_title = show.get("title", "AI Post Transformers")

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = (
        f"{show_title} \u2014 Paper Queue")
    ET.SubElement(channel, "description").text = (
        "Two-lens editorial ranking of AI research papers under "
        f"consideration for upcoming {show_title} episodes.")
    ET.SubElement(channel, "link").text = show.get(
        "link", "https://podcast.do-not-panic.com")
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc))

    section_labels = {
        "bridge": "Bridge",
        "public": "Public AI",
        "memory": "Memory/Storage",
        "monitor": "Monitor",
        "deferred": "Deferred",
        "out_of_scope": "Out of Scope",
    }

    total = 0
    for section_key, label in section_labels.items():
        for rec in sections.get(section_key, []):
            item = ET.SubElement(channel, "item")
            badge_str = ""
            if rec.badges:
                badge_str = " [" + ", ".join(rec.badges) + "]"
            ET.SubElement(item, "title").text = (
                f"[{label}] {rec.title}{badge_str}")
            ET.SubElement(item, "link").text = rec.url

            abstract = rec.abstract
            if len(abstract) > 500:
                abstract = abstract[:497] + "..."
            desc_parts = [abstract]
            desc_parts.append(
                f"\nPublic: {rec.public_interest_score:.3f} | "
                f"Memory: {rec.memory_score:.3f} | "
                f"Status: {rec.status}")
            if rec.why_now:
                desc_parts.append(f"Why now: {rec.why_now}")
            ET.SubElement(item, "description").text = (
                "\n".join(desc_parts))

            if rec.published_at:
                try:
                    dt = datetime.fromisoformat(
                        rec.published_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ET.SubElement(item, "pubDate").text = (
                        format_datetime(dt))
                except (ValueError, TypeError):
                    pass

            guid = ET.SubElement(item, "guid", isPermaLink="false")
            guid.text = rec.arxiv_id
            total += 1

    feed_path = Path(__file__).parent / "podcasts" / "queue.xml"
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(str(feed_path), xml_declaration=True,
               encoding="UTF-8")
    _log("[Queue]", f"RSS   -> {_c('dim', str(feed_path))} "
         f"({total} items)", "blue")
    return str(feed_path)


# Section descriptions for tooltip help text
_SECTION_DESCS = {
    "Bridge": (
        "Papers scoring high on both lenses \u2014 broad public AI "
        "significance and memory/storage systems relevance. These "
        "make the strongest episodes because they connect "
        "foundational AI advances to real systems constraints."),
    "Public AI": (
        "Papers ranking high on broad AI significance. These "
        "change how the field thinks about models, training, "
        "evaluation, or deployment \u2014 strong episode candidates "
        "for a general AI audience."),
    "Memory/Storage": (
        "Papers ranking high on memory, storage, and data "
        "movement relevance. These address bandwidth, KV cache, "
        "offload, quantization, tiering, or scheduling \u2014 "
        "systems bottlenecks that increasingly constrain AI "
        "workloads."),
    "Monitor": (
        "Interesting papers that scored well enough to review "
        "but were not scheduled this cycle. They may be promoted "
        "in a future run if the topic becomes more timely or if "
        "queue slots open up."),
    "Deferred": (
        "Papers the LLM reviewer judged as relevant but not "
        "urgent enough to cover now. They may fit better in a "
        "future cycle when the topic landscape shifts or related "
        "work appears."),
    "Out of Scope": (
        "Papers the LLM reviewer judged as outside this show's "
        "editorial lenses. Typically narrow-domain work, pure "
        "theory without clear systems relevance, or papers that "
        "mention AI keywords without genuinely advancing the "
        "field. Shown here for algorithm auditing."),
}


# Badge CSS class mapping
_BADGE_CSS = {
    "Bridge": "badge-bridge",
    "Public AI": "badge-public",
    "Memory/Storage Core": "badge-memory",
    "Memory/Storage Adjacent": "badge-memory",
    "Systems": "badge-systems",
    "Theory": "badge-theory",
    "Hardware": "badge-hardware",
    "Training": "badge-training",
    "Inference": "badge-inference",
    "Application": "badge-application",
}

# Status CSS class mapping
_STATUS_CSS = {
    "Cover now": "status-cover",
    "Monitor": "status-monitor",
    "Deferred this cycle": "status-deferred",
    "Out of scope": "status-out-of-scope",
}


def _render_section_table(section_name, records, accent_color,
                          section_desc=""):
    """Render an HTML section with header and table rows."""
    if not records:
        return ""

    section_id = section_name.lower().replace("/", "-").replace(" ", "-")
    rows = []
    for i, rec in enumerate(records, 1):
        authors = rec.authors
        if isinstance(authors, list):
            if len(authors) > 3:
                authors_str = ", ".join(authors[:3]) + " et al."
            else:
                authors_str = ", ".join(authors)
        else:
            authors_str = str(authors)

        published = rec.published_at
        if published:
            try:
                dt = datetime.fromisoformat(
                    published.replace("Z", "+00:00"))
                published = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        # Badge pills
        badge_pills = ""
        for badge in rec.badges:
            css_cls = _BADGE_CSS.get(badge, "badge-systems")
            badge_pills += (
                f' <span class="badge {css_cls}">'
                f'{html.escape(badge)}</span>')

        # Status label
        status_cls = _STATUS_CSS.get(rec.status, "status-deferred")
        status_html = (
            f'<span class="status-label {status_cls}">'
            f'{html.escape(rec.status)}</span>')

        # Tooltip content
        tip_lines = []
        tip_lines.append(
            f'<div class="tip-line">Public: '
            f'{rec.public_interest_score:.3f} | Memory: '
            f'{rec.memory_score:.3f}</div>')
        tip_lines.append(
            f'<div class="tip-line">Quality: '
            f'{rec.quality_score:.3f} | Bridge: '
            f'{rec.bridge_score:.3f}</div>')
        tip_lines.append(
            f'<div class="tip-taxonomy">Scope: '
            f'{html.escape(rec.scope_bucket)} | '
            f'Domain: {html.escape(rec.domain_bucket)} | '
            f'Type: {html.escape(rec.paper_type)}</div>')
        tip_lines.append('<hr class="tip-divider">')
        tip_lines.append(
            f'<div class="tip-scores">Public lens &nbsp; '
            f'broad={rec.broad_relevance:.2f} '
            f'momentum={rec.momentum:.2f} '
            f'teach={rec.teachability:.2f}<br>'
            f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
            f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
            f'novelty={rec.novelty_score:.2f} '
            f'evidence={rec.evidence_score:.2f}</div>')
        tip_lines.append(
            f'<div class="tip-scores">Memory lens &nbsp; '
            f'direct={rec.direct_memory_relevance:.2f} '
            f'systems={rec.systems_leverage:.2f} '
            f'deploy={rec.deployment_proximity:.2f}<br>'
            f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
            f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'
            f'adjacent={rec.memory_adjacent_future_value:.2f} '
            f'bandwidth={rec.bandwidth_capacity:.2f}</div>')
        if rec.why_now:
            tip_lines.append('<hr class="tip-divider">')
            tip_lines.append(
                f'<div class="tip-line tip-hf">'
                f'{html.escape(rec.why_now)}</div>')
        if rec.why_not_higher:
            tip_lines.append(
                f'<div class="tip-line tip-penalty">'
                f'{html.escape(rec.why_not_higher)}</div>')
        if rec.downgrade_reasons:
            reasons_str = ", ".join(rec.downgrade_reasons)
            tip_lines.append(
                f'<div class="tip-line tip-penalty">'
                f'{html.escape(reasons_str)}</div>')
        if rec.what_would_raise_priority:
            tip_lines.append(
                f'<div class="tip-line tip-dim">'
                f'{html.escape(rec.what_would_raise_priority)}'
                f'</div>')
        if rec.one_sentence_episode_hook:
            tip_lines.append(
                f'<div class="tip-line tip-keyword">'
                f'{html.escape(rec.one_sentence_episode_hook)}'
                f'</div>')
        # Social/influencer signals
        if rec.social_score > 0 or rec.influencer_matches:
            tip_lines.append('<hr class="tip-divider">')
            tip_lines.append(
                f'<div class="tip-scores">Social: '
                f'score={rec.social_score:.2f} '
                f'influencer={rec.influencer_boost:.2f} '
                f'pwc={"Y" if rec.pwc_trending_flag else "N"}'
                f'</div>')
            if rec.influencer_matches:
                names = ", ".join(rec.influencer_matches[:5])
                tip_lines.append(
                    f'<div class="tip-line tip-hf">'
                    f'Influencers: {html.escape(names)}'
                    f'</div>')
        # Time window + compound scoring
        if rec.time_window and rec.time_window != "30d":
            tip_lines.append(
                f'<div class="tip-line tip-dim">'
                f'Window: {html.escape(rec.time_window)} '
                f'compound_boost={rec.compound_window_boost:.2f}'
                f'</div>')
        # Scoring sources transparency
        if rec.scoring_sources:
            sources_str = ", ".join(rec.scoring_sources[:8])
            tip_lines.append(
                f'<div class="tip-line" style="color:#666;">'
                f'Sources: {html.escape(sources_str)}'
                f'</div>')
        tooltip = "\n".join(tip_lines)

        rows.append(
            f'<tr>'
            f'<td class="rank">{i}</td>'
            f'<td><a href="{html.escape(rec.url)}" '
            f'target="_blank">'
            f'{html.escape(rec.title)}</a>{badge_pills}</td>'
            f'<td class="authors">'
            f'{html.escape(authors_str)}</td>'
            f'<td class="date">'
            f'{html.escape(published)}</td>'
            f'<td class="score">'
            f'{rec.public_interest_score:.3f}'
            f'<div class="score-tip">'
            f'<div class="tip-header">Score breakdown</div>'
            f'{tooltip}</div></td>'
            f'<td class="score">'
            f'{rec.memory_score:.3f}</td>'
            f'<td>{status_html}</td>'
            f'</tr>')

    rows_html = "\n".join(rows)

    # Section header with optional tooltip
    if section_desc:
        header_html = (
            f'<div class="section-header-wrap">'
            f'<h2 class="section-header"'
            f' style="border-left: 5px solid {accent_color};'
            f' padding-left: 0.8rem;">'
            f'<span style="color:{accent_color}">'
            f'{html.escape(section_name)}</span>'
            f' <span class="section-count">{len(records)}</span>'
            f' <span class="section-help">(?)</span></h2>'
            f'<div class="section-tip">'
            f'{html.escape(section_desc)}</div></div>')
    else:
        header_html = (
            f'<h2 class="section-header"'
            f' style="border-left: 5px solid {accent_color};'
            f' padding-left: 0.8rem;">'
            f'<span style="color:{accent_color}">'
            f'{html.escape(section_name)}</span>'
            f' <span class="section-count">{len(records)}</span>'
            f'</h2>')

    return f"""
<div class="queue-section" id="{section_id}">
  {header_html}
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Title</th>
        <th>Authors</th>
        <th>Published</th>
        <th class="score-header">Public</th>
        <th class="score-header">Memory</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
{rows_html}
    </tbody>
  </table>
</div>"""


def generate_queue_html_v2(sections, config):
    """Generate the slim queue UI as the canonical public queue.html."""
    import re

    template_path = Path(__file__).parent / "podcasts" / "queue2.html"
    html_path = Path(__file__).parent / "podcasts" / "queue.html"
    page = template_path.read_text()

    def _record_to_jsonable(r):
        if isinstance(r, dict):
            return r
        if hasattr(r, "to_dict"):
            return r.to_dict()
        if hasattr(r, "__dict__"):
            return dict(r.__dict__)
        return r

    slim_sections = {
        "public": [_record_to_jsonable(r) for r in sections.get("public", [])],
        "memory": [_record_to_jsonable(r) for r in sections.get("memory", [])],
        "bridge": [_record_to_jsonable(r) for r in sections.get("bridge", [])],
        "monitor": [_record_to_jsonable(r) for r in sections.get("monitor", [])],
        "deferred": [_record_to_jsonable(r) for r in sections.get("deferred", [])],
        "out_of_scope": [_record_to_jsonable(r) for r in sections.get("out_of_scope", [])],
    }
    queue_json = json.dumps(slim_sections, ensure_ascii=False)

    page, n = re.subn(
        r"const QUEUE = .*?;\n\nconst CATEGORY_META =",
        f"const QUEUE = {queue_json};\n\nconst CATEGORY_META =",
        page,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise RuntimeError("Failed to inject fresh queue data into podcasts/queue2.html")

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page)
    total = sum(len(v) for v in slim_sections.values())
    _log("[Queue]", f"HTML  -> {_c('dim', str(html_path))} "
         f"({total} papers, slim)", "blue")
    return str(html_path)
