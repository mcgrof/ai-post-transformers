"""Podcast generation workflow using ElevenLabs Studio API."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser

from db import (
    get_connection, init_db, get_podcast_arxiv_ids,
    insert_podcast, update_podcast, link_podcast_paper, list_podcasts,
    get_today_papers,
)
from elevenlabs_client import create_podcast, finalize_podcast
from image_gen import generate_episode_image
from pdf_utils import download_and_extract
from rss import generate_feed


def _format_source_citation(papers_info):
    """Format source citation text for a podcast description.

    Args:
        papers_info: List of dicts, each with optional keys: title, authors,
                     published, url, institution.

    Returns:
        Formatted citation string.
    """
    if not papers_info:
        return ""

    header = "Source:" if len(papers_info) == 1 else "Sources:"
    sections = []

    for i, info in enumerate(papers_info, 1):
        lines = []
        if len(papers_info) > 1:
            lines.append(f"{i})")

        # Date
        published = info.get("published", "")
        if published:
            try:
                dt = datetime.fromisoformat(published)
                lines.append(dt.strftime("%B %-d %Y"))
            except (ValueError, TypeError):
                pass

        # Title
        title = info.get("title", "")
        if title:
            lines.append(title)

        # Institution (if ever available)
        institution = info.get("institution", "")
        if institution:
            lines.append(institution)

        # Authors
        authors = info.get("authors", "")
        if authors:
            if isinstance(authors, list):
                authors = ", ".join(authors)
            lines.append(authors)

        # URL
        url = info.get("url", "")
        if url:
            lines.append(url)

        if lines:
            sections.append("\n".join(lines))

    return header + "\n\n" + "\n\n".join(sections)


def _find_paper(conn, arxiv_id):
    """Look up a paper by arxiv_id in the database."""
    row = conn.execute(
        "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def _pick_top_paper(conn):
    """Pick the highest-scoring paper not yet covered in a podcast."""
    podcasted = get_podcast_arxiv_ids(conn)
    papers = get_today_papers(conn)
    if not papers:
        # Fall back to any papers in the DB
        rows = conn.execute(
            "SELECT * FROM papers ORDER BY score DESC"
        ).fetchall()
        papers = [dict(r) for r in rows]

    for p in papers:
        if p["arxiv_id"] not in podcasted:
            return p
    return None


def _prepare_podcast_text(paper):
    """Prepare the text input for ElevenLabs podcast generation."""
    title = paper["title"]
    abstract = paper.get("abstract", "")
    authors = paper.get("authors", "")
    if isinstance(authors, list):
        authors = ", ".join(authors)
    url = paper.get("arxiv_url", "")

    text = f"""Paper: {title}

Authors: {authors}

Abstract: {abstract}

Link: {url}"""
    return text


def _build_image_prompt(config, title, abstract_or_summary):
    """Build a prompt for episode image generation.

    Args:
        config: Full application config dict.
        title: Paper or episode title.
        abstract_or_summary: Abstract text or extracted summary.

    Returns:
        Combined prompt string.
    """
    img_config = config.get("image_generation", {})
    style = img_config.get("style_prompt", "").strip()
    context = f"Research topic: {title}"
    if abstract_or_summary:
        context += f"\n\n{abstract_or_summary[:500]}"
    if style:
        return f"{style}\n\n{context}"
    return context


def _generate_episode_image(config, episode_title, abstract_or_summary,
                            audio_file_path):
    """Generate an episode cover image if enabled in config.

    Args:
        config: Full application config dict.
        episode_title: Title for the episode / paper.
        abstract_or_summary: Abstract or text excerpt for prompt context.
        audio_file_path: Path to the audio file (image uses same stem, .png).

    Returns:
        Image file path string on success, None on failure or if disabled.
    """
    img_config = config.get("image_generation", {})
    if not img_config.get("enabled", False):
        return None

    prompt = _build_image_prompt(config, episode_title, abstract_or_summary)
    model = img_config.get("model", "gpt-image-1")
    size = img_config.get("size", "1024x1024")
    quality = img_config.get("quality", "high")

    audio_path = Path(audio_file_path)
    image_path = audio_path.with_suffix(".png")

    try:
        generate_episode_image(prompt, str(image_path), model=model,
                               size=size, quality=quality)
        return str(image_path)
    except Exception as e:
        print(f"[Podcast] Warning: Image generation failed: {e}",
              file=sys.stderr)
        return None


def generate_podcast(config, arxiv_id=None):
    """Generate a podcast episode from a paper.

    Args:
        config: Full application config dict.
        arxiv_id: Optional specific arXiv ID. If None, picks the top uncovered paper.
    """
    conn = get_connection()
    init_db(conn)

    # Select paper
    if arxiv_id:
        paper = _find_paper(conn, arxiv_id)
        if not paper:
            print(f"Error: Paper {arxiv_id} not found in database.", file=sys.stderr)
            print("Run a digest first to populate the database.", file=sys.stderr)
            conn.close()
            return
    else:
        paper = _pick_top_paper(conn)
        if not paper:
            print("Error: No uncovered papers found. Run a digest first.", file=sys.stderr)
            conn.close()
            return

    print(f"[Podcast] Selected paper: {paper['title']}", file=sys.stderr)
    print(f"[Podcast] arXiv ID: {paper['arxiv_id']}", file=sys.stderr)

    # Prepare text
    text = _prepare_podcast_text(paper)

    # Create podcast via ElevenLabs
    tmpdir, list_file, segments = create_podcast(text, config)
    finalize_podcast(tmpdir, list_file, str(audio_file))

    # Generate episode cover image
    image_file = _generate_episode_image(
        config, paper["title"], paper.get("abstract", ""), str(audio_file)
    )

    # Build description with source citation
    authors = paper.get("authors", [])
    if isinstance(authors, str):
        authors = json.loads(authors)
    paper_info = {
        "title": paper.get("title", ""),
        "authors": authors,
        "published": paper.get("published", ""),
        "url": paper.get("arxiv_url", ""),
    }
    description = _format_source_citation([paper_info])

    # Record in database
    title = f"Episode: {paper['title']}"
    podcast_id = insert_podcast(
        conn,
        title=title,
        publish_date=date_str,
        elevenlabs_project_id=project_id,
        audio_file=str(audio_file),
        description=description,
        image_file=image_file,
    )
    link_podcast_paper(conn, podcast_id, paper["arxiv_id"])
    conn.close()

    generate_feed(config)

    print(f"\nPodcast generated successfully!", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  Audio: {audio_file}", file=sys.stderr)
    print(f"  Paper: {paper['arxiv_url']}", file=sys.stderr)


def show_generated_podcast_list(top=None):
    """Display locally generated podcast episodes, one per line.

    Args:
        top: If set, limit output to the latest N episodes.
    """
    conn = get_connection()
    init_db(conn)
    episodes = list_podcasts(conn)
    conn.close()

    if not episodes:
        print("No podcasts generated yet.")
        return

    if top is not None:
        episodes = episodes[:top]

    for ep in episodes:
        print(f"{ep['publish_date']}  {ep['title']}")


def fetch_anchor_rss(anchor_rss_url):
    """Fetch and parse the public Anchor RSS feed.

    Args:
        anchor_rss_url: URL of the Anchor/Spotify RSS feed.

    Returns:
        List of dicts with keys: title, publish_date, url — sorted newest-first.
    """
    feed = feedparser.parse(anchor_rss_url)
    episodes = []
    for entry in feed.entries:
        pub = entry.get("published_parsed")
        if pub:
            publish_date = datetime(*pub[:3]).strftime("%Y-%m-%d")
        else:
            publish_date = ""
        episodes.append({
            "title": entry.get("title", ""),
            "publish_date": publish_date,
            "url": entry.get("link", ""),
        })
    episodes.sort(key=lambda e: e["publish_date"], reverse=True)
    return episodes


def _normalize_title(title):
    """Normalize a title for fuzzy matching.

    Strips 'Episode: ' prefix, lowercases, and collapses whitespace.
    """
    t = re.sub(r"^Episode:\s*", "", title, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t.lower()).strip()


def show_public_podcast_list(config, top=None):
    """Show all public episodes from the Anchor RSS feed.

    Local (tool-generated) episodes are marked with [local].

    Args:
        config: Full application config dict.
        top: If set, limit output to the latest N episodes.
    """
    anchor_rss_url = config.get("spotify", {}).get("anchor_rss", "")
    if not anchor_rss_url:
        print("Error: spotify.anchor_rss not configured in config.yaml",
              file=sys.stderr)
        sys.exit(1)

    public_episodes = fetch_anchor_rss(anchor_rss_url)
    if not public_episodes:
        print("No episodes found in RSS feed.")
        return

    # Load local episode titles for [local] matching
    conn = get_connection()
    init_db(conn)
    local_episodes = list_podcasts(conn)
    conn.close()
    local_titles = {_normalize_title(ep["title"]) for ep in local_episodes}

    if top is not None:
        public_episodes = public_episodes[:top]

    for ep in public_episodes:
        marker = "  [local]" if _normalize_title(ep["title"]) in local_titles else ""
        print(f"{ep['publish_date']}  {ep['title']}{marker}")


def _combine_papers_text(papers_text, max_total=100_000):
    """Combine multiple papers' text with delimiters, truncating per-paper.

    Args:
        papers_text: List of (url, text) tuples.
        max_total: Maximum total character count.

    Returns:
        Combined text string with paper delimiters.
    """
    num_papers = len(papers_text)
    per_paper_limit = max_total // num_papers

    sections = []
    for i, (url, text) in enumerate(papers_text, 1):
        if len(text) > per_paper_limit:
            print(f"[Podcast] Truncating paper {i} from {len(text)} to "
                  f"{per_paper_limit} chars", file=sys.stderr)
            text = text[:per_paper_limit]
        sections.append(f"=== PAPER {i} ===\nSource: {url}\n\n{text}")

    return "\n\n".join(sections)


def _get_multi_paper_instructions(config, num_papers, goal=None):
    """Get podcast instructions for multi-paper episodes.

    Args:
        config: Full application config dict.
        num_papers: Number of papers being discussed.
        goal: Optional focus topic to prepend to instructions.

    Returns:
        Instructions string for ElevenLabs.
    """
    podcast_config = config.get("podcast", {})
    template = podcast_config.get("multi_paper_instructions", "")
    if template:
        instructions = template.format(num_papers=num_papers).strip()
    else:
        # Fallback if no multi-paper template configured
        instructions = (
            f"You are two AI researchers discussing {num_papers} papers together. "
            f"Identify common themes across all papers, explain why they are interesting "
            f"together, then discuss each paper's contribution. Keep the discussion "
            f"accessible but technically accurate."
        )
    if goal:
        instructions = f"FOCUS: {goal}\n\n{instructions}"
    return instructions


def generate_podcast_from_urls(urls, config, goal=None):
    """Generate a podcast episode from one or more PDF URLs.

    Args:
        urls: List of PDF URLs.
        config: Full application config dict.
        goal: Optional focus topic injected into podcast instructions.
    """
    # Download and extract text from all PDFs
    papers_text = []
    for url in urls:
        text = download_and_extract(url)
        if not text.strip():
            print(f"[Podcast] Warning: No text extracted from {url}, skipping",
                  file=sys.stderr)
            continue
        papers_text.append((url, text))

    if not papers_text:
        print("Error: No text could be extracted from any of the provided URLs.",
              file=sys.stderr)
        sys.exit(1)

    # Build source text
    num_papers = len(papers_text)
    if num_papers == 1:
        source_text = papers_text[0][1]
    else:
        source_text = _combine_papers_text(papers_text)

    # Override instructions for multi-paper or goal-focused episodes
    if num_papers > 1:
        instructions = _get_multi_paper_instructions(config, num_papers, goal=goal)
        config = {**config, "podcast": {**config.get("podcast", {}),
                                         "instructions": instructions}}
    elif goal:
        base = config.get("podcast", {}).get("instructions", "")
        instructions = f"FOCUS: {goal}\n\n{base}" if base else f"FOCUS: {goal}"
        config = {**config, "podcast": {**config.get("podcast", {}),
                                         "instructions": instructions}}

    print(f"[Podcast] Generating podcast from {num_papers} paper(s) "
          f"({len(source_text)} chars total)", file=sys.stderr)

    # Create podcast via ElevenLabs
    tmpdir, list_file, segments = create_podcast(source_text, config)
    finalize_podcast(tmpdir, list_file, str(audio_file))

    # Record in database
    conn = get_connection()
    init_db(conn)

    if num_papers == 1:
        title = f"Episode: {urls[0]}"
    else:
        title = f"Episode: {num_papers} papers combined"

    # Generate episode cover image
    # Build summary from extracted text for image prompt context
    if num_papers == 1:
        image_summary = papers_text[0][1][:500]
    else:
        per_paper = 500 // num_papers
        image_summary = " ".join(text[:per_paper] for _, text in papers_text)
    image_file = _generate_episode_image(
        config, title, image_summary, str(audio_file)
    )

    # Build description with source citations from URLs
    papers_info = [{"url": url} for url, _ in papers_text]
    description = _format_source_citation(papers_info)

    podcast_id = insert_podcast(
        conn,
        title=title,
        publish_date=date_str,
        elevenlabs_project_id=project_id,
        audio_file=str(audio_file),
        source_urls=json.dumps(urls),
        description=description,
        image_file=image_file,
    )
    conn.close()

    generate_feed(config)

    print(f"\nPodcast generated successfully!", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  Audio: {audio_file}", file=sys.stderr)
    for url in urls:
        print(f"  Source: {url}", file=sys.stderr)
