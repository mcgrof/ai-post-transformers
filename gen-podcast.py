#!/usr/bin/env python3
"""gen-podcast: Generate podcasts from PDF URLs or manage paper digests.

Primary usage: pass one or more PDF URLs to generate a podcast episode.
Multiple PDFs produce a combined episode that identifies common themes.

Examples:
    gen-podcast.py URL [URL ...]                              # from URLs
    gen-podcast.py URL [URL ...] --goal "focus on X"          # with goal
    gen-podcast.py --input-papers papers.txt                  # from file
    gen-podcast.py --input-papers papers.txt --goal "focus"   # file + goal
    gen-podcast.py digest                                     # daily digest
    gen-podcast.py podcast [--paper 2401.12345]               # from DB paper
    gen-podcast.py --list-podcasts                            # public episodes
    gen-podcast.py --list-generated-podcasts                  # local episodes
    gen-podcast.py --list-podcasts --top 5                    # latest 5
"""

import argparse
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

from sources.arxiv_source import fetch_arxiv_papers
from sources.hf_daily import fetch_hf_daily_papers
from sources.semantic import enrich_papers
from interests import InterestScorer
from db import (
    get_connection, init_db, paper_exists, upsert_paper,
    get_podcast_arxiv_ids,
)


def load_config():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def generate_markdown(papers, date_str):
    """Generate markdown digest file."""
    lines = [
        f"# AI Paper Digest - {date_str}",
        f"",
        f"*{len(papers)} papers selected from arXiv, ranked by relevance*",
        f"",
    ]
    for i, p in enumerate(papers, 1):
        authors_str = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors_str += " et al."
        lines.append(f"## {i}. {p['title']}")
        lines.append(f"")
        lines.append(f"**Authors:** {authors_str}")
        lines.append(f"**Score:** {p['score']:.3f} | **Why:** {p['score_reason']}")
        hf_tag = " | **HF Trending**" if p.get("hf_daily") else ""
        cit_tag = f" | **Citations:** {p.get('citation_count', 0)}" if p.get("citation_count") else ""
        lines.append(f"**Link:** {p['arxiv_url']}{hf_tag}{cit_tag}")
        lines.append(f"")

    return "\n".join(lines)


def generate_whatsapp(papers, date_str):
    """Generate WhatsApp-friendly plain text digest."""
    lines = [
        f"AI Paper Digest - {date_str}",
        f"{len(papers)} papers ranked by relevance",
        "",
    ]
    for i, p in enumerate(papers, 1):
        authors_str = ", ".join(p["authors"][:2])
        if len(p["authors"]) > 2:
            authors_str += " et al."
        hf_flag = " [HF trending]" if p.get("hf_daily") else ""
        lines.append(f"{i}. {p['title']}")
        lines.append(f"   {authors_str}")
        lines.append(f"   Score: {p['score']:.2f} - {p['score_reason']}")
        lines.append(f"   {p['arxiv_url']}{hf_flag}")
        lines.append("")

    return "\n".join(lines)


def run_digest(config):
    """Run the paper digest pipeline."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scoring_config = config.get("scoring", {})
    top_n = scoring_config.get("top_n", 20)

    # Init DB early to get podcasted IDs for scoring
    conn = get_connection()
    init_db(conn)
    podcasted_ids = get_podcast_arxiv_ids(conn)

    # 1. Fetch papers from arXiv
    categories = config.get("arxiv_categories", [])
    papers = fetch_arxiv_papers(categories)

    if not papers:
        print("No papers fetched from arXiv. Exiting.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # 2. Check HuggingFace Daily Papers for trending signal
    hf_ids = fetch_hf_daily_papers()
    for p in papers:
        if p["arxiv_id"] in hf_ids:
            p["hf_daily"] = True

    # 3. Enrich with Semantic Scholar citation data
    papers = enrich_papers(papers)

    # 4. Score against interest profile
    scorer = InterestScorer(config, podcasted_ids=podcasted_ids)
    papers = scorer.score_papers(papers)

    # 5. Take top N
    top_papers = papers[:top_n]

    # 6. Store in DB
    for p in top_papers:
        p["digest_date"] = date_str
        upsert_paper(conn, p)
    conn.close()

    # 7. Generate outputs
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    md = generate_markdown(top_papers, date_str)
    md_path = output_dir / f"{date_str}.md"
    md_path.write_text(md)
    print(f"[Output] Markdown digest: {md_path}", file=sys.stderr)

    whatsapp = generate_whatsapp(top_papers, date_str)
    print(whatsapp)


def _read_urls_from_file(filepath):
    """Read URLs from a file, one per line. Blank lines and #comments skipped."""
    urls = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


SUBCOMMANDS = {"digest", "podcast", "spotify-upload"}


def main():
    parser = argparse.ArgumentParser(
        prog="gen-podcast.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate podcasts from PDF URLs or manage paper digests.",
        epilog="""\
modes:
  URL [URL ...]                        generate podcast from PDF URLs
  URL [URL ...] --goal "focus on X"    with a focus topic
  --input-papers FILE                  read URLs from a file
  digest                               run the daily paper digest
  podcast [--paper ARXIV_ID]           generate podcast from a DB paper
  --list-podcasts                      list public episodes from Anchor RSS
  --list-generated-podcasts            list locally generated episodes
  --top N                              limit listing to latest N episodes
  spotify-upload                       regenerate RSS feed for Spotify
""",
    )
    parser.add_argument(
        "args", nargs="*", metavar="URL_OR_COMMAND",
        help="PDF URLs or a subcommand (digest, podcast)",
    )
    parser.add_argument(
        "--input-papers", metavar="FILE",
        help="file with one PDF URL per line (blank lines and #comments skipped)",
    )
    parser.add_argument(
        "--goal", metavar="TEXT",
        help="focus topic injected into podcast instructions",
    )
    parser.add_argument(
        "--paper", metavar="ARXIV_ID",
        help="arXiv paper ID (used with the 'podcast' subcommand)",
    )
    parser.add_argument(
        "--list-podcasts", action="store_true",
        help="list all public episodes from the Anchor RSS feed",
    )
    parser.add_argument(
        "--list-generated-podcasts", action="store_true",
        help="list locally generated podcast episodes",
    )
    parser.add_argument(
        "--top", type=int, metavar="N",
        help="limit listing to the latest N episodes",
    )

    args = parser.parse_args()

    # Mutual exclusivity check
    if args.list_podcasts and args.list_generated_podcasts:
        parser.error("--list-podcasts and --list-generated-podcasts are mutually exclusive")

    # --top requires a listing flag
    if args.top is not None and not (args.list_podcasts or args.list_generated_podcasts):
        parser.error("--top requires --list-podcasts or --list-generated-podcasts")

    # No args and no flags → print help
    if (not args.args and not args.input_papers
            and not args.list_podcasts and not args.list_generated_podcasts):
        parser.print_help()
        sys.exit(0)

    config = load_config()

    # Flag-based listing takes priority over subcommands
    if args.list_podcasts:
        from podcast import show_public_podcast_list
        show_public_podcast_list(config, top=args.top)
        sys.exit(0)

    if args.list_generated_podcasts:
        from podcast import show_generated_podcast_list
        show_generated_podcast_list(top=args.top)
        sys.exit(0)

    # Detect subcommand: first positional arg is a known subcommand name
    command = args.args[0] if args.args and args.args[0] in SUBCOMMANDS else None

    if command == "digest":
        run_digest(config)
    elif command == "podcast":
        from podcast import generate_podcast
        generate_podcast(config, arxiv_id=args.paper)
    elif command == "spotify-upload":
        from rss import generate_feed
        feed_path = generate_feed(config)
        spotify = config.get("spotify", {})
        audio_base_url = spotify.get("audio_base_url", "")
        print(f"Feed file: {feed_path}")
        if audio_base_url:
            feed_file = spotify.get("feed_file", "podcasts/feed.xml")
            print(f"Expected remote URL: {audio_base_url.rstrip('/')}/{feed_file}")
    else:
        # URL mode: collect from positional args + --input-papers
        urls = list(args.args)
        if args.input_papers:
            urls.extend(_read_urls_from_file(args.input_papers))
        if not urls:
            print("Error: No URLs provided.", file=sys.stderr)
            sys.exit(1)
        from podcast import generate_podcast_from_urls
        generate_podcast_from_urls(urls, config, goal=args.goal)


if __name__ == "__main__":
    main()
