"""Podcast generation workflow using ElevenLabs Studio API."""

import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import feedparser

from db import (
    get_connection, init_db, get_podcast_arxiv_ids,
    insert_podcast, update_podcast, link_podcast_paper, list_podcasts,
    get_today_papers, get_covered_topics, add_covered_topics,
)
from draft_revisions import detect_revision, assign_revision
from elevenlabs_client import create_podcast, finalize_podcast, save_transcript, generate_srt, cleanup_podcast_tmpdir
from image_gen import generate_episode_image
from pdf_utils import download_and_extract
from rss import generate_feed


def _make_episode_stem(title, date_str, urls=None):
    """Build unique filename stem: {date}-{slug}-{hash6}."""
    slug = unicodedata.normalize("NFKD", title)
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)[:40].rstrip("-")
    slug = re.sub(r"^episode-", "", slug)
    hash_input = f"{title}|{date_str}|{','.join(sorted(urls or []))}"
    hash6 = hashlib.sha256(hash_input.encode()).hexdigest()[:6]
    return f"{date_str}-{slug}-{hash6}"


def _get_output_dir(date_str):
    """Return drafts/YYYY/MM/ directory, creating if needed."""
    year, month = date_str.split("-")[:2]
    output_dir = Path(__file__).parent / "drafts" / year / month
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _rename_episode_files(old_audio, new_stem):
    """Rename all episode files (.mp3, .txt, .srt, .json, .png) to new_stem.

    Args:
        old_audio: Path to the current .mp3 file.
        new_stem: New filename stem (without extension).

    Returns:
        New audio Path object, or old_audio if rename failed.
    """
    old_path = Path(old_audio)
    output_dir = old_path.parent
    old_stem = old_path.stem

    renamed = []
    for ext in [".mp3", ".txt", ".srt", ".json", ".png"]:
        old_file = output_dir / f"{old_stem}{ext}"
        new_file = output_dir / f"{new_stem}{ext}"
        if old_file.exists():
            old_file.rename(new_file)
            renamed.append(ext)

    if renamed:
        print(f"[Podcast] Renamed files: {old_stem} → {new_stem} "
              f"({', '.join(renamed)})", file=sys.stderr)
        return output_dir / f"{new_stem}.mp3"
    return old_path


def _save_generation_inputs(title, date_str, urls, goal=None,
                            description_guidance=None):
    """Save podcast generation inputs to a versioned directory.

    Creates inputs/YYYY/MM/DD/{slug}-v{N}-{hash6}/ with:
      - urls.txt, goal.txt, description.txt

    Returns:
        Path to the created inputs directory.
    """
    year, month, day = date_str.split("-")[:3]
    base_dir = Path(__file__).parent / "inputs" / year / month / day

    # Build slug (same logic as _make_episode_stem but shorter for dir name)
    slug = unicodedata.normalize("NFKD", title)
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug)[:30].rstrip("-")
    slug = re.sub(r"^episode-", "", slug)

    hash_input = f"{title}|{date_str}|{','.join(sorted(urls or []))}"
    hash6 = hashlib.sha256(hash_input.encode()).hexdigest()[:6]

    # Find next version number
    version = 1
    if base_dir.exists():
        for d in base_dir.iterdir():
            if d.is_dir() and d.name.startswith(f"{slug}-v"):
                try:
                    # Parse version from e.g. "slug-v3-abc123"
                    parts = d.name[len(slug) + 2:].split("-", 1)
                    v = int(parts[0])
                    version = max(version, v + 1)
                except (ValueError, IndexError):
                    pass

    inputs_dir = base_dir / f"{slug}-v{version}-{hash6}"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # Write input files
    (inputs_dir / "urls.txt").write_text("\n".join(urls) + "\n")
    if goal:
        (inputs_dir / "goal.txt").write_text(goal + "\n")
    if description_guidance:
        (inputs_dir / "description.txt").write_text(description_guidance + "\n")

    print(f"[Podcast] Inputs saved → {inputs_dir}", file=sys.stderr)
    return inputs_dir


def _generate_title(script, config):
    """Generate a short episode title from the podcast script.

    Returns a concise title (5-10 words) suitable for filenames
    and RSS feed display.
    """
    from llm_backend import get_llm_backend, llm_call

    backend = get_llm_backend(config)
    model = config.get("podcast", {}).get("llm_model", "sonnet")

    transcript = "\n".join([
        f"{'Hal' if s['speaker'] == 'A' else 'Ada'}: {s['text']}"
        for s in script[:20]
    ])

    prompt = f"""Based on this podcast transcript, write a short episode title (5-10 words).
The title should capture the main topic or paper being discussed.
Do NOT include "Episode:", podcast name, or host names.
Do NOT use quotes around the title.
Output ONLY the title text, nothing else.

Transcript:
{transcript[:4000]}"""

    title = llm_call(backend, model, prompt, temperature=0.2,
                     max_tokens=50, json_mode=False)
    # Clean up: strip quotes, "Episode:" prefix, whitespace
    title = title.strip().strip('"\'')
    title = re.sub(r'^(Episode:\s*)', '', title, flags=re.IGNORECASE)
    return title


def _generate_summary(script, config, guidance=None):
    """Generate an episode summary from the podcast script transcript.

    Args:
        script: List of script segment dicts with 'speaker' and 'text'.
        config: Config dict with podcast.llm_model.
        guidance: Optional custom guidance for the summary style/content.
    """
    from llm_backend import get_llm_backend, llm_call

    backend = get_llm_backend(config)
    model = config.get("podcast", {}).get("llm_model", "sonnet")

    # Build transcript from script
    transcript = "\n".join([
        f"{'Hal Turing' if s['speaker'] == 'A' else 'Dr. Ada Shannon'}: {s['text']}"
        for s in script[:40]  # First 40 segments should cover the key content
    ])

    if guidance:
        prompt = f"""Write a podcast episode description based on this transcript.

CUSTOM GUIDANCE (follow this closely for tone, structure, and content):
{guidance}

Write 3 paragraphs. Be specific about the content discussed. Write in third person.

Transcript:
{transcript[:6000]}

Output ONLY the description text, no quotes or labels."""
    else:
        prompt = f"""Write a concise podcast episode summary (3-5 sentences) based on this transcript.
Focus on: what topics are covered, key findings or arguments discussed, and why a listener
would find it interesting. Write in third person ("This episode explores...").
Do NOT mention the podcast name or the host names. Be specific about the content — no generic filler.

Transcript:
{transcript[:6000]}

Output ONLY the summary text, no quotes or labels."""

    return llm_call(backend, model, prompt, temperature=0.3,
                    max_tokens=600, json_mode=False)


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


def _extract_arxiv_id(url):
    """Extract arXiv id from common arxiv URL forms."""
    if not url:
        return None
    m = re.search(r'arxiv\.org/(?:abs|pdf|html)/([^?#]+)', url)
    if not m:
        return None
    arxiv_id = m.group(1)
    arxiv_id = arxiv_id.replace('.pdf', '')
    return arxiv_id


def _resolve_source_info(conn, url, fallback_title=""):
    """Resolve a source URL into title/authors/year/url metadata when possible."""
    info = {"title": fallback_title or url, "authors": "", "year": "", "url": url}
    arxiv_id = _extract_arxiv_id(url)
    if not arxiv_id:
        return info

    paper = _find_paper(conn, arxiv_id)
    if paper is None:
        try:
            from paper_queue import _fetch_arxiv_meta
            paper = _fetch_arxiv_meta(arxiv_id)
            if paper:
                # keep DB warm so future lookups are cheap
                from db import upsert_paper
                upsert_paper(conn, paper)
                conn.commit()
        except Exception:
            paper = None

    if not paper:
        info["title"] = fallback_title or arxiv_id
        return info

    authors = paper.get("authors", "")
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except Exception:
            pass
    year = ""
    published = paper.get("published", "")
    if published:
        year = str(published)[:4]

    info.update({
        "title": paper.get("title") or fallback_title or arxiv_id,
        "authors": ", ".join(authors) if isinstance(authors, list) else (authors or ""),
        "year": year,
        "url": paper.get("arxiv_url") or url,
    })
    return info


def _format_source_entry(index, title, authors, year, url):
    line = f"  {index}. {title}"
    tail = []
    if authors:
        tail.append(authors)
    if year:
        tail.append(year)
    if tail:
        line += f" — {', '.join(tail)}"
    lines = [line]
    if url:
        lines.append(f"     {url}")
    return lines


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


def _build_image_prompt(config, title, description):
    """Build a prompt for episode infographic image generation.

    Args:
        config: Full application config dict.
        title: Paper or episode title.
        description: Episode description (summary text).

    Returns:
        Combined prompt string.
    """
    img_config = config.get("image_generation", {})
    style = img_config.get("style_prompt", "").strip()

    # Extract key stats/points from description for infographic
    prompt = f"""{style}

Create a DARK-THEMED INFOGRAPHIC for a podcast episode cover image.

Episode: {title}

Content to visualize:
{description[:800]}

DESIGN REQUIREMENTS:
- Dark background (deep navy/charcoal/black) with vibrant accent colors (cyan, orange, electric blue)
- Do NOT include the podcast name or host names — they are already implied
- Include 3-5 KEY STATS or FINDINGS as short text callouts with icons/symbols
- Use clean data visualization elements (simple charts, percentage rings, arrows)
- Modern minimalist infographic style — NOT cluttered
- Text must be LEGIBLE and SPELLED CORRECTLY
- 1024x1024 square format suitable for podcast feed
- Professional, tech-forward aesthetic"""

    return prompt


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
                               size=size, quality=quality, config=config)
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

    # Output path
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = _get_output_dir(date_str)
    stem = _make_episode_stem(paper['title'], date_str)
    audio_file = output_dir / f"{stem}.mp3"

    # Get previously covered topics for context
    covered_topics = get_covered_topics(conn)

    # Create podcast via ElevenLabs TTS (multi-pass pipeline)
    tmpdir, list_file, segments, sources, topic_names, script = create_podcast(
        text, config, covered_topics
    )
    try:
        finalize_podcast(tmpdir, list_file, str(audio_file))

        # Save transcript and SRT subtitles
        hosts = config.get("podcast", {}).get("hosts", {})
        host_names = {
            "A": hosts.get("a", {}).get("name", "Hal Turing"),
            "B": hosts.get("b", {}).get("name", "Dr. Ada Shannon"),
        }
        transcript_file = audio_file.with_suffix(".txt")
        srt_file = audio_file.with_suffix(".srt")
        save_transcript(script, str(transcript_file), host_names)
        generate_srt(script, segments, str(srt_file), host_names=host_names)

        # Save script JSON for future use
        script_file = audio_file.with_suffix(".json")
        with open(script_file, "w") as f:
            json.dump({"script": script, "sources": sources, "topics": topic_names}, f, indent=2)

        # Record new topics as covered
        if topic_names:
            add_covered_topics(conn, topic_names)
    finally:
        # Always clean up /tmp/podcast_* even if something failed — the
        # per-segment files are useless once the final MP3 is written.
        cleanup_podcast_tmpdir(tmpdir)

    # Build description with source citations (primary paper + all referenced works)
    authors = paper.get("authors", [])
    if isinstance(authors, str):
        authors = json.loads(authors)
    paper_info = {
        "title": paper.get("title", ""),
        "authors": authors,
        "published": paper.get("published", ""),
        "url": paper.get("arxiv_url", ""),
    }

    # Generate episode summary from transcript
    print("[Podcast] Generating episode summary...", file=sys.stderr)
    summary = _generate_summary(script, config)

    # Format description: summary + numbered sources with URLs
    desc_lines = [summary, "", "Sources:"]
    src_num = 1
    desc_lines.extend(_format_source_entry(
        src_num,
        paper_info['title'],
        ', '.join(authors) if authors else '',
        paper_info.get('published', '')[:4],
        paper_info.get('url', ''),
    ))
    src_num += 1

    if sources:
        for s in sources:
            title_str = s.get('title', '?')
            authors_str = s.get('authors', '?')
            year_str = s.get('year', '?')
            url_str = s.get('url', '')
            if not url_str:
                url_str = f"https://scholar.google.com/scholar?q={title_str.replace(' ', '+')}"
            desc_lines.append(f"  {src_num}. {title_str} — {authors_str}, {year_str}")
            desc_lines.append(f"     {url_str}")
            src_num += 1

    description = "\n".join(desc_lines)

    # Generate episode cover image (uses description for infographic content)
    image_file = _generate_episode_image(
        config, paper["title"], summary, str(audio_file)
    )

    # Record in database — store the bare title. The "Episode: "
    # prefix used to be applied here but it polluted the manifest
    # ("Episode: Speculative Speculative Decoding") and the public
    # feed. _generate_title() in the URL-based path explicitly
    # strips this prefix; the legacy --paper path should never
    # have added it.
    title = paper['title']
    podcast_id = insert_podcast(
        conn,
        title=title,
        publish_date=date_str,
        elevenlabs_project_id="tts-local",
        audio_file=str(audio_file),
        description=description,
        image_file=image_file,
    )
    link_podcast_paper(conn, podcast_id, paper["arxiv_id"])

    # Assign revision tracking
    ep_key, rev, superseded = detect_revision(conn, title,
                                              exclude_id=podcast_id)
    assign_revision(conn, podcast_id, ep_key, rev, superseded)
    if rev > 1:
        print(f"[Podcast] Revision v{rev} of logical episode "
              f"'{ep_key}' (superseded {len(superseded)} older)",
              file=sys.stderr)

    conn.close()

    print(f"\nPodcast generated successfully!", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  Audio: {audio_file}", file=sys.stderr)
    print(f"  Paper: {paper['arxiv_url']}", file=sys.stderr)
    if sources:
        print(f"  Sources referenced: {len(sources)}", file=sys.stderr)
        for s in sources:
            print(f"    - {s.get('title', '?')} ({s.get('year', '?')})", file=sys.stderr)


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


def generate_podcast_from_urls(urls, config, goal=None, description_guidance=None):
    """Generate a podcast episode from one or more PDF URLs.

    Args:
        urls: List of PDF URLs.
        config: Full application config dict.
        goal: Optional focus topic injected into podcast instructions.
    """
    # Download and extract text from all PDFs
    papers_text = []
    for url in urls:
        try:
            text = download_and_extract(url)
        except Exception as exc:
            print(
                f"[Podcast] Warning: Failed to extract text from {url}: {exc}",
                file=sys.stderr,
            )
            continue
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

    # Temporary title for initial filenames (renamed after script generation)
    tmp_title = f"wip-{num_papers}-papers"

    # Output path (temporary, will be renamed once real title is known)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = _get_output_dir(date_str)
    stem = _make_episode_stem(tmp_title, date_str, urls=urls)
    audio_file = output_dir / f"{stem}.mp3"

    # Get previously covered topics
    conn = get_connection()
    init_db(conn)
    covered_topics = get_covered_topics(conn)

    # Create podcast via ElevenLabs (multi-pass pipeline)
    tmpdir, list_file, segments, sources, topic_names, script = create_podcast(
        source_text, config, covered_topics
    )
    try:
        finalize_podcast(tmpdir, list_file, str(audio_file))

        # Generate real title from the script content
        print("[Podcast] Generating episode title...", file=sys.stderr)
        title = _generate_title(script, config)
        print(f"[Podcast] Title: {title}", file=sys.stderr)

        # Rename files from temporary stem to title-based stem
        new_stem = _make_episode_stem(title, date_str, urls=urls)
        if new_stem != stem:
            audio_file = _rename_episode_files(audio_file, new_stem)

        # Save generation inputs with the real title
        _save_generation_inputs(title, date_str, urls, goal=goal,
                                description_guidance=description_guidance)

        # Save transcript and SRT subtitles
        hosts = config.get("podcast", {}).get("hosts", {})
        host_names = {
            "A": hosts.get("a", {}).get("name", "Hal Turing"),
            "B": hosts.get("b", {}).get("name", "Dr. Ada Shannon"),
        }
        transcript_file = audio_file.with_suffix(".txt")
        srt_file = audio_file.with_suffix(".srt")
        save_transcript(script, str(transcript_file), host_names)
        generate_srt(script, segments, str(srt_file), host_names=host_names)

        # Save script JSON
        script_file = audio_file.with_suffix(".json")
        with open(script_file, "w") as f:
            json.dump({"script": script, "sources": sources, "topics": topic_names}, f, indent=2)

        # Record new topics
        if topic_names:
            add_covered_topics(conn, topic_names)
    finally:
        # Always clean up /tmp/podcast_* even if something failed.
        cleanup_podcast_tmpdir(tmpdir)

    # Generate episode summary from transcript
    print("[Podcast] Generating episode summary...", file=sys.stderr)
    summary = _generate_summary(script, config, guidance=description_guidance)

    # Build description: summary + numbered sources with URLs
    desc_lines = [summary, "", "Sources:"]
    src_num = 1
    for i, url in enumerate(urls):
        fallback_title = title if i == 0 else ""
        source_info = _resolve_source_info(conn, url, fallback_title=fallback_title)
        desc_lines.extend(_format_source_entry(
            src_num,
            source_info.get('title', url),
            source_info.get('authors', ''),
            source_info.get('year', ''),
            source_info.get('url', url),
        ))
        src_num += 1
    if sources:
        for s in sources:
            title_str = s.get('title', '?')
            authors_str = s.get('authors', '?')
            year_str = s.get('year', '?')
            url_str = s.get('url', '')
            if not url_str:
                url_str = f"https://scholar.google.com/scholar?q={title_str.replace(' ', '+')}"
            desc_lines.append(f"  {src_num}. {title_str} — {authors_str}, {year_str}")
            desc_lines.append(f"     {url_str}")
            src_num += 1
    description = "\n".join(desc_lines)

    # Generate episode cover image (uses summary for infographic content)
    image_file = _generate_episode_image(
        config, title, summary, str(audio_file)
    )

    podcast_id = insert_podcast(
        conn,
        title=title,
        publish_date=date_str,
        elevenlabs_project_id="tts-local",
        audio_file=str(audio_file),
        source_urls=json.dumps(urls),
        description=description,
        image_file=image_file,
    )

    # Assign revision tracking
    ep_key, rev, superseded = detect_revision(conn, title,
                                              source_urls=json.dumps(urls),
                                              exclude_id=podcast_id)
    assign_revision(conn, podcast_id, ep_key, rev, superseded)
    if rev > 1:
        print(f"[Podcast] Revision v{rev} of logical episode "
              f"'{ep_key}' (superseded {len(superseded)} older)",
              file=sys.stderr)

    conn.close()

    print(f"\nPodcast generated successfully!", file=sys.stderr)
    print(f"  Title: {title}", file=sys.stderr)
    print(f"  Audio: {audio_file}", file=sys.stderr)
    for url in urls:
        print(f"  Source: {url}", file=sys.stderr)
    if sources:
        print(f"  References: {len(sources)}", file=sys.stderr)
