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
    from sources.arxiv_source import fetch_arxiv_papers

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
                "publish-site", "gen-viz"}


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
  gen-viz                              generate visualizations for episodes
  gen-viz --draft STEM                 generate viz for a specific episode
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
    parser.add_argument(
        "--llm-backend", choices=["openai", "claude-cli", "anthropic"],
        help="override podcast.llm_backend for this run only",
    )
    parser.add_argument(
        "--llm-model", metavar="MODEL",
        help="override podcast.llm_model for this run only",
    )
    parser.add_argument(
        "--analysis-model", metavar="MODEL",
        help="override podcast.analysis_model for this run only",
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

    # --draft requires publish/gen-viz subcommand or --publish
    if args.draft and not args.publish:
        if not (args.args and args.args[0] in ("publish", "gen-viz")):
            parser.error("--draft requires the 'publish' or 'gen-viz' subcommand")

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
    elif command == "gen-viz":
        from viz_gen import generate_viz, upload_viz, update_episode_viz_link
        _run_gen_viz(config, args)
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


def _run_gen_viz(config, args):
    """Generate visualizations for episodes.

    With --draft, generates for a specific episode. Without it,
    generates for recent episodes that lack a viz link.
    """
    from viz_gen import generate_viz, upload_viz, update_episode_viz_link
    import os

    if args.draft:
        episode = _find_episode_by_draft(args.draft)
        if not episode:
            print(f"No episode found matching: {args.draft}",
                  file=sys.stderr)
            sys.exit(1)
        episodes = [episode]
    else:
        conn = get_connection()
        init_db(conn)
        from db import list_podcasts
        all_eps = list_podcasts(conn)
        conn.close()
        # Select recent episodes without viz links
        episodes = []
        for ep in all_eps:
            desc = ep.get("description") or ""
            if "podcast.do-not-panic.com/viz/" not in desc:
                episodes.append(ep)
            if len(episodes) >= 5:
                break

    for ep in episodes:
        ep_id = ep.get("id")
        try:
            viz_path = generate_viz(ep_id, config)
            if viz_path:
                url = upload_viz(viz_path)
                slug = os.path.splitext(os.path.basename(viz_path))[0]
                update_episode_viz_link(ep_id, slug)
                print(f"[Viz] {ep.get('title', 'Untitled')}: {url}",
                      file=sys.stderr)
        except Exception as e:
            print(f"[Viz] Error for episode {ep_id}: {e}",
                  file=sys.stderr)


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
    project_root = os.path.dirname(os.path.abspath(__file__))

    static_files = [
        ("index.html", "index.html", "text/html"),
        ("sister-podcasts.html",      "sister-podcasts.html",      "text/html"),
        ("sponsor.html",              "sponsor.html",              "text/html"),
        ("queue.html",                "queue.html",                "text/html"),
        ("queue.xml",                 "queue.xml",                 "application/xml"),
        ("images/podcast-bg.png",     "images/podcast-bg.png",     "image/png"),
        ("images/queue-bg.png",       "images/queue-bg.png",       "image/png"),
        ("images/spotify.png",        "images/spotify.png",        "image/png"),
        ("images/apple-podcasts.png", "images/apple-podcasts.png", "image/png"),
        ("images/amazon-podcasts.png","images/amazon-podcasts.png","image/png"),
        ("images/rss-feed.png",       "images/rss-feed.png",       "image/png"),
        ("images/paper-queue.png",    "images/paper-queue.png",    "image/png"),
        ("images/sister-podcasts.png","images/sister-podcasts.png","image/png"),
        ("images/ai-origins.jpg",     "images/ai-origins.jpg",     "image/jpeg"),
        ("images/ai-ax.jpg",          "images/ai-ax.jpg",          "image/jpeg"),
    ]
    for local_name, r2_key, ctype in static_files:
        local_path = os.path.join(feed_dir, local_name)
        if not os.path.exists(local_path):
            local_path = os.path.join(project_root, local_name)
        if os.path.exists(local_path):
            url = upload_file(r2, local_path, r2_key, content_type=ctype)
            print(f"{_c('35', '[Site]')} {_c('1', r2_key)}: "
                  f"{_c('2', url)}", file=sys.stderr)

    # Upload GitHub icon if present
    gh_icon = os.path.join(project_root, "images", "github.png")
    if os.path.exists(gh_icon):
        url = upload_file(r2, gh_icon, "images/github.png",
                          content_type="image/png")
        print(f"{_c('35', '[Site]')} {_c('1', 'images/github.png')}: "
              f"{_c('2', url)}", file=sys.stderr)

    import glob as globmod

    # Upload generated thumbnails
    for thumb in sorted(globmod.glob(os.path.join(feed_dir, "thumbs", "*.webp"))):
        rel = os.path.relpath(thumb, feed_dir)
        url = upload_file(r2, thumb, rel, content_type="image/webp")
        print(f"{_c('35', '[Site]')} {_c('1', rel)}: "
              f"{_c('2', url)}", file=sys.stderr)

    # Upload archive pages (YYYY/MM/index.html)
    for archive_page in sorted(globmod.glob(
            os.path.join(feed_dir, "[0-9][0-9][0-9][0-9]",
                         "[0-9][0-9]", "index.html"))):
        rel = os.path.relpath(archive_page, feed_dir)
        url = upload_file(r2, archive_page, rel, content_type="text/html")
        print(f"{_c('35', '[Site]')} {_c('1', rel)}: "
              f"{_c('2', url)}", file=sys.stderr)

    # Upload individual episode pages (episodes/{slug}/index.html)
    # R2 doesn't auto-resolve directory indexes, so upload at both keys
    ep_pages = sorted(globmod.glob(
            os.path.join(feed_dir, "episodes", "*", "index.html")))
    print(f"{_c('35', '[Site]')} Uploading {len(ep_pages)} episode pages...",
          file=sys.stderr)
    for ep_page in ep_pages:
        rel = os.path.relpath(ep_page, feed_dir)
        slug_dir = os.path.dirname(rel)  # episodes/{slug}
        upload_file(r2, ep_page, rel, content_type="text/html")
        # Also upload as episodes/{slug}/ so bare URL works
        upload_file(r2, ep_page, slug_dir + "/",
                    content_type="text/html")
    print(f"{_c('35', '[Site]')} {len(ep_pages)} episode pages uploaded",
          file=sys.stderr)

    # Upload conference pages (conference/{id}/index.html)
    conf_pages = sorted(globmod.glob(
            os.path.join(feed_dir, "conference", "*", "index.html")))
    print(f"{_c('35', '[Site]')} Uploading {len(conf_pages)} conference pages...",
          file=sys.stderr)
    for conf_page in conf_pages:
        rel = os.path.relpath(conf_page, feed_dir)
        conf_dir = os.path.dirname(rel)
        upload_file(r2, conf_page, rel, content_type="text/html")
        upload_file(r2, conf_page, conf_dir + "/",
                    content_type="text/html")
    print(f"{_c('35', '[Site]')} {len(conf_pages)} conference pages uploaded",
          file=sys.stderr)

    # Upload all viz HTML files
    viz_dir = os.path.join(project_root, "viz")
    if os.path.isdir(viz_dir):
        for viz_file in sorted(globmod.glob(os.path.join(viz_dir, "*.html"))):
            r2_key = f"viz/{os.path.basename(viz_file)}"
            url = upload_file(r2, viz_file, r2_key,
                              content_type="text/html")
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

    # Set publish_date to today and published_at to now (approval/publish ordering)
    from datetime import date, datetime, timezone
    today = date.today().isoformat()
    published_at = datetime.now(timezone.utc).isoformat()
    pub_date = today
    conn_pub = get_connection()
    init_db(conn_pub)
    conn_pub.execute("UPDATE podcasts SET publish_date = ?, published_at = ? WHERE id = ?",
                     (today, published_at, episode.get("id")))
    conn_pub.commit()

    # Mark revision as published and supersede older revisions
    from draft_revisions import mark_published
    ep_key = mark_published(conn_pub, episode.get("id"))
    if ep_key:
        print(f"[Publish] Revision state: published (episode_key={ep_key})", file=sys.stderr)

    conn_pub.close()
    print(f"[Publish] publish_date set to {today}; published_at={published_at}", file=sys.stderr)

    # Copy episode files to public/YYYY/MM/ and update DB paths
    if pub_date:
        year, month = pub_date.split("-")[:2]
        public_dir = Path(__file__).parent / "public" / year / month
        public_dir.mkdir(parents=True, exist_ok=True)
        stem = os.path.splitext(audio)[0]
        for src in glob.glob(f"{stem}.*"):
            dst = public_dir / os.path.basename(src)
            try:
                shutil.copy2(src, dst)
                print(f"[Publish] Copied → {dst}", file=sys.stderr)
            except shutil.SameFileError:
                print(f"[Publish] Already at {dst}", file=sys.stderr)

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

            # Clean up draft files (already copied to public/)
            for src in glob.glob(f"{stem}.*"):
                os.remove(src)
                print(f"[Publish] Removed draft: {src}",
                      file=sys.stderr)

    # Generate and upload visualization (non-fatal on failure)
    try:
        from viz_gen import generate_viz, upload_viz, update_episode_viz_link
        viz_path = generate_viz(episode.get("id"), config)
        if viz_path:
            viz_url = upload_viz(viz_path)
            slug = os.path.splitext(os.path.basename(viz_path))[0]
            update_episode_viz_link(episode.get("id"), slug)
            print(f"[Publish] Viz: {viz_url}", file=sys.stderr)
    except Exception as e:
        print(f"[Publish] Viz generation skipped: {e}", file=sys.stderr)

    # Regenerate and upload RSS feed + site pages/assets
    feed_path = generate_feed(config)
    feed_url = upload_feed(feed_path)
    print(f"[Publish] Feed: {feed_url}", file=sys.stderr)
    _publish_site(config)

    # Remove from admin drafts + delete R2 draft mp3 now that publish succeeded
    try:
        import boto3, json, os as _os
        s3 = boto3.client(
            "s3",
            endpoint_url=_os.environ["AWS_ENDPOINT_URL"],
            aws_access_key_id=_os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=_os.environ["AWS_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        draft_id = episode.get("id")
        manifest_obj = s3.get_object(Bucket="podcast-admin", Key="manifest.json")
        manifest = json.loads(manifest_obj["Body"].read())
        before = len(manifest.get("drafts", []))
        manifest["drafts"] = [d for d in manifest.get("drafts", []) if d.get("id") != draft_id]
        s3.put_object(Bucket="podcast-admin", Key="manifest.json", Body=json.dumps(manifest, indent=2), ContentType="application/json")
        try:
            s3.delete_object(Bucket="ai-post-transformers", Key=f"drafts/ep{draft_id}.mp3")
        except Exception:
            pass
        print(f"[Publish] Admin manifest cleaned: {before} -> {len(manifest.get('drafts', []))}", file=sys.stderr)
    except Exception as e:
        print(f"[Publish] Admin cleanup skipped: {e}", file=sys.stderr)

    print(f"\nPublished!", file=sys.stderr)


if __name__ == "__main__":
    main()
