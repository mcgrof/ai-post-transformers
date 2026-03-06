#!/usr/bin/env python3
"""gen-podcast: Generate podcasts from PDF URLs or manage paper digests.

Primary usage: pass one or more PDF URLs to generate a podcast episode.
Multiple PDFs produce a combined episode that identifies common themes.

Examples:
    gen-podcast.py URL [URL ...]                              # from URLs
    gen-podcast.py URL [URL ...] --goal "focus on X"          # with goal
    gen-podcast.py URL --goal-file goal.txt                   # goal from file
    gen-podcast.py URL --goal-file g.txt --description-file d.txt
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


def _read_text_file(filepath):
    """Read and return stripped text content from a file."""
    with open(filepath) as f:
        return f.read().strip()


SUBCOMMANDS = {"digest", "podcast", "queue", "spotify-upload", "publish",
                "publish-site", "viz-sync"}


def main():
    parser = argparse.ArgumentParser(
        prog="gen-podcast.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate podcasts from PDF URLs or manage paper digests.",
        epilog="""\
modes:
  URL [URL ...]                        generate podcast from PDF URLs
  URL [URL ...] --goal "focus on X"    with a focus topic
  URL --goal-file goal.txt             goal from file (keeps record)
  --input-papers FILE                  read URLs from a file
  digest                               run the daily paper digest
  podcast [--paper ARXIV_ID]           generate podcast from a DB paper
  --list-podcasts                      list public episodes from Anchor RSS
  --list-generated-podcasts            list locally generated episodes
  --top N                              limit listing to latest N episodes
  publish                              publish latest episode to R2
  publish --draft drafts/2026/03/stem  publish a specific draft
  spotify-upload                       regenerate RSS feed for Spotify
  viz-sync                             sync visualization catalogs
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
        "--goal-file", metavar="FILE",
        help="read goal text from a file (mutually exclusive with --goal)",
    )
    parser.add_argument(
        "--description", metavar="TEXT",
        help="custom description guidance for the episode summary",
    )
    parser.add_argument(
        "--description-file", metavar="FILE",
        help="read description guidance from a file (mutually exclusive with --description)",
    )
    parser.add_argument(
        "--paper", metavar="ARXIV_ID",
        help="arXiv paper ID (used with the 'podcast' subcommand)",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="upload episode to R2 and regenerate RSS feed after generation",
    )
    parser.add_argument(
        "--draft", metavar="DIR_OR_STEM",
        help="draft directory or file stem to publish (use with 'publish' subcommand)",
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

    # Resolve --goal-file / --description-file (file takes precedence, error if both)
    if args.goal and args.goal_file:
        parser.error("--goal and --goal-file are mutually exclusive")
    if args.description and args.description_file:
        parser.error("--description and --description-file are mutually exclusive")
    if args.goal_file:
        args.goal = _read_text_file(args.goal_file)
    if args.description_file:
        args.description = _read_text_file(args.description_file)

    # Mutual exclusivity check
    if args.list_podcasts and args.list_generated_podcasts:
        parser.error("--list-podcasts and --list-generated-podcasts are mutually exclusive")

    # --top requires a listing flag
    if args.top is not None and not (args.list_podcasts or args.list_generated_podcasts):
        parser.error("--top requires --list-podcasts or --list-generated-podcasts")

    # --draft requires publish subcommand or --publish
    if args.draft and not args.publish:
        if not (args.args and args.args[0] == "publish"):
            parser.error("--draft requires the 'publish' subcommand")

    # No args and no flags → print help
    if (not args.args and not args.input_papers
            and not args.list_podcasts and not args.list_generated_podcasts
            and not args.publish):
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
    elif command == "queue":
        from paper_queue import run_queue
        run_queue(config)
    elif command == "podcast":
        from podcast import generate_podcast
        generate_podcast(config, arxiv_id=args.paper)
    elif command == "publish":
        _publish_episode(config, draft=args.draft)
        return
    elif command == "publish-site":
        _publish_site(config)
        return
    elif command == "viz-sync":
        from viz_catalog import run_viz_sync
        run_viz_sync(config)
        return
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
        generate_podcast_from_urls(urls, config, goal=args.goal, description_guidance=args.description)

    # --publish after generation
    if args.publish:
        _publish_episode(config, draft=args.draft)


def _find_episode_by_draft(draft_path):
    """Find a DB episode matching a draft path or stem.

    Args:
        draft_path: Path to a draft file, directory, or file stem.
                    e.g. "drafts/2026/03/2026-03-04-4-papers-combined-8a237d"
                    or   "drafts/2026/03/2026-03-04-4-papers-combined-8a237d.mp3"

    Returns:
        Episode dict from DB, or None.
    """
    from db import get_connection, init_db, list_podcasts
    import os

    # Normalize: strip extensions, resolve to absolute
    draft_path = str(draft_path).rstrip("/")
    for ext in [".mp3", ".txt", ".json", ".srt", ".png"]:
        if draft_path.endswith(ext):
            draft_path = draft_path[:-len(ext)]
            break
    stem = os.path.basename(draft_path)

    conn = get_connection()
    init_db(conn)
    episodes = list_podcasts(conn)
    conn.close()

    for ep in episodes:
        audio = ep.get("audio_file", "")
        if stem in audio:
            return ep
    return None


def _c(code, text):
    """ANSI color helper for stderr."""
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return text
    return f"\033[{code}m{text}\033[0m"


def _publish_site(config):
    """Upload static site files to R2: feed, index, queue, background."""
    from r2_upload import get_r2_client, upload_file, upload_feed
    from rss import generate_feed
    import os

    feed_path = generate_feed(config)
    feed_url = upload_feed(feed_path)
    print(f"{_c('35', '[Site]')} Feed: {_c('2', feed_url)}",
          file=sys.stderr)

    r2 = get_r2_client()
    feed_dir = os.path.dirname(feed_path)

    static_files = [
        ("index.html",                "index.html",                "text/html"),
        ("about.html",                "about.html",                "text/html"),
        ("queue.html",                "queue.html",                "text/html"),
        ("queue.xml",                 "queue.xml",                 "application/xml"),
        ("images/podcast-bg.png",     "images/podcast-bg.png",     "image/png"),
        ("images/queue-bg.png",       "images/queue-bg.png",       "image/png"),
        ("images/spotify.png",        "images/spotify.png",        "image/png"),
        ("images/apple-podcasts.png", "images/apple-podcasts.png", "image/png"),
        ("images/amazon-podcasts.png","images/amazon-podcasts.png","image/png"),
        ("images/rss-feed.png",       "images/rss-feed.png",       "image/png"),
        ("images/paper-queue.png",    "images/paper-queue.png",    "image/png"),
        ("images/ai-origins.jpg",     "images/ai-origins.jpg",     "image/jpeg"),
        ("images/ai-ax.jpg",          "images/ai-ax.jpg",          "image/jpeg"),
    ]
    for local_name, r2_key, ctype in static_files:
        local_path = os.path.join(feed_dir, local_name)
        if os.path.exists(local_path):
            url = upload_file(r2, local_path, r2_key, content_type=ctype)
            print(f"{_c('35', '[Site]')} {_c('1', r2_key)}: "
                  f"{_c('2', url)}", file=sys.stderr)

    print(f"\n{_c('32', 'Site published!')}", file=sys.stderr)


def _publish_episode(config, draft=None):
    """Upload an episode to R2 and regenerate the RSS feed.

    Requires --draft to specify which draft to publish.
    """
    from r2_upload import publish_episode, upload_feed
    from rss import generate_feed
    import glob
    import os
    import shutil

    if not draft:
        print("Error: publish requires --draft to specify which episode.",
              file=sys.stderr)
        sys.exit(1)

    episode = _find_episode_by_draft(draft)
    if not episode:
        print(f"No episode found matching draft: {draft}",
              file=sys.stderr)
        sys.exit(1)

    audio = episode.get("audio_file", "")
    if not audio or not os.path.exists(audio):
        print(f"Audio file not found: {audio}", file=sys.stderr)
        return

    image = episode.get("image_file")
    # Check for image alongside audio if not in DB
    if not image or not os.path.exists(image):
        candidate = os.path.splitext(audio)[0] + ".png"
        if os.path.exists(candidate):
            image = candidate
    srt = os.path.splitext(audio)[0] + ".srt" if audio else None

    print(f"\n[Publish] Uploading: {episode.get('title', 'Untitled')}",
          file=sys.stderr)
    urls = publish_episode(audio, image_file=image, srt_file=srt)

    for k, v in urls.items():
        print(f"[Publish] {k}: {v}", file=sys.stderr)

    # Copy episode files to public/YYYY/MM/ and update DB paths
    pub_date = episode.get("publish_date", "")
    if pub_date:
        year, month = pub_date.split("-")[:2]
        public_dir = Path(__file__).parent / "public" / year / month
        public_dir.mkdir(parents=True, exist_ok=True)
        stem = os.path.splitext(audio)[0]
        for src in glob.glob(f"{stem}.*"):
            dst = public_dir / os.path.basename(src)
            shutil.copy2(src, dst)
            print(f"[Publish] Copied → {dst}", file=sys.stderr)

        # Update DB so audio/image point to public/ (needed for feed)
        if "/drafts/" in audio:
            new_audio = audio.replace("/drafts/", "/public/")
            new_image = image.replace("/drafts/", "/public/") if image and "/drafts/" in image else image
            conn = get_connection()
            init_db(conn)
            cur = conn.cursor()
            cur.execute("UPDATE podcasts SET audio_file = ?, image_file = ? WHERE id = ?",
                        (new_audio, new_image, episode.get("id")))
            conn.commit()
            conn.close()
            print(f"[Publish] DB updated: drafts → public", file=sys.stderr)

    # Regenerate and upload RSS feed + index page
    feed_path = generate_feed(config)
    feed_url = upload_feed(feed_path)
    print(f"[Publish] Feed: {feed_url}", file=sys.stderr)

    # Upload index.html and static assets alongside feed
    from r2_upload import get_r2_client, upload_file
    feed_dir = os.path.dirname(feed_path)
    r2 = get_r2_client()

    index_path = os.path.join(feed_dir, "index.html")
    if os.path.exists(index_path):
        idx_url = upload_file(r2, index_path, "index.html",
                              content_type="text/html")
        print(f"[Publish] Index: {idx_url}", file=sys.stderr)

    bg_path = os.path.join(feed_dir, "images", "podcast-bg.png")
    if os.path.exists(bg_path):
        bg_url = upload_file(r2, bg_path, "images/podcast-bg.png",
                             content_type="image/png")
        print(f"[Publish] Background: {bg_url}", file=sys.stderr)

    print(f"\nPublished!", file=sys.stderr)


if __name__ == "__main__":
    main()
