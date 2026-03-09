"""LLM-generated interactive visualization pages for podcast episodes.

Generates standalone HTML pages with analysis, key findings, and
cross-references from the podcast discussion. Each page uses inline
CSS/JS with the podcast's dark theme and links to arXiv for all
papers mentioned in the transcript.
"""

import os
import re
import sys
from pathlib import Path

from db import get_connection, init_db, list_podcasts, update_podcast
from llm_backend import get_llm_backend, llm_call


def _c(code, text):
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return text
    return f"\033[{code}m{text}\033[0m"


def _parse_srt(srt_path):
    """Strip sequence numbers and timestamps from SRT, return plain text."""
    lines = Path(srt_path).read_text().splitlines()
    text_lines = []
    for line in lines:
        line = line.strip()
        # Skip sequence numbers (bare integers)
        if re.match(r'^\d+$', line):
            continue
        # Skip timestamp lines
        if re.match(r'^\d{2}:\d{2}:\d{2}', line):
            continue
        if line:
            text_lines.append(line)
    return " ".join(text_lines)


def _build_viz_prompt(title, description, transcript, arxiv_ids):
    """Build the LLM prompt for generating a visualization HTML page."""
    arxiv_section = ""
    if arxiv_ids:
        id_list = ", ".join(arxiv_ids)
        arxiv_section = f"""
Known source paper arXiv IDs: {id_list}
Extract any additional arXiv IDs mentioned in the transcript (pattern: DDDD.DDDDD).
"""

    return f"""Generate a standalone HTML page that serves as an interactive
visualization companion for a podcast episode. The page should analyze
and present the key findings, technical details, and cross-references
discussed in the episode.

EPISODE TITLE: {title}
EPISODE DESCRIPTION: {description or 'N/A'}
{arxiv_section}
DESIGN REQUIREMENTS:
- Standalone HTML with ALL CSS and JS inline (no external dependencies
  except Google Fonts)
- Google Fonts to load: Syne (headings), JetBrains Mono (code/data)
- Dark theme matching the podcast site:
  - Background: #080c14
  - Text: #c9d8ec
  - Accent: #00d4ff
  - Secondary accent: #ff6b35
  - Card/section backgrounds: #0d1520
  - Border: #1a2535
- Responsive layout (works on mobile and desktop)
- Smooth scroll behavior

STRUCTURAL REQUIREMENTS:
1. Header with episode title and brief overview
2. Key Findings section — the most important takeaways
3. Technical Deep-Dive — detailed breakdown of methods, architectures,
   or concepts discussed, with diagrams rendered in CSS/SVG where helpful
4. Cross-References — all papers mentioned in the discussion, with
   arXiv links (https://arxiv.org/abs/XXXX.XXXXX) for each
5. A "Listen to the Episode" section at the bottom linking back to
   https://podcast.do-not-panic.com

For every paper mentioned by name, author, or ID in the transcript,
include a properly formatted arXiv link. Extract IDs from the
transcript text itself.

TRANSCRIPT:
{transcript[:30000]}

OUTPUT: Return ONLY the complete HTML document, starting with <!DOCTYPE html>
and ending with </html>. No markdown fences, no explanation text."""


def generate_viz(episode_id, config):
    """Generate an interactive visualization for a podcast episode.

    Looks up the episode in the DB, finds its SRT transcript, calls the
    LLM to generate a standalone HTML page, and saves it to viz/.

    Args:
        episode_id: The podcast episode ID from the DB.
        config: The application config dict.

    Returns:
        Path to the generated HTML file, or None on failure.
    """
    conn = get_connection()
    init_db(conn)
    episodes = list_podcasts(conn)
    conn.close()

    episode = None
    for ep in episodes:
        if ep.get("id") == episode_id:
            episode = ep
            break

    if not episode:
        print(f"{_c('31', '[Viz]')} Episode {episode_id} not found",
              file=sys.stderr)
        return None

    audio = episode.get("audio_file", "")
    if not audio:
        print(f"{_c('31', '[Viz]')} No audio file for episode {episode_id}",
              file=sys.stderr)
        return None

    srt_path = os.path.splitext(audio)[0] + ".srt"
    if not os.path.exists(srt_path):
        print(f"{_c('31', '[Viz]')} SRT not found: {srt_path}",
              file=sys.stderr)
        return None

    transcript = _parse_srt(srt_path)
    if not transcript:
        print(f"{_c('31', '[Viz]')} Empty transcript: {srt_path}",
              file=sys.stderr)
        return None

    # Extract arXiv IDs from source_urls
    arxiv_ids = []
    import json
    source_urls = episode.get("source_urls")
    if source_urls:
        try:
            for url in json.loads(source_urls):
                m = re.search(r'(\d{4}\.\d{4,5})', url)
                if m:
                    arxiv_ids.append(m.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    title = episode.get("title", "Untitled")
    description = episode.get("description", "")

    print(f"{_c('35', '[Viz]')} Generating visualization for: {title}",
          file=sys.stderr)

    prompt = _build_viz_prompt(title, description, transcript, arxiv_ids)

    backend = get_llm_backend(config)
    model = config.get("podcast", {}).get("llm_model", "sonnet")
    html_content = llm_call(backend, model, prompt,
                            temperature=0.4, max_tokens=16000,
                            json_mode=False)

    # Strip any markdown fences the LLM may have wrapped around the HTML
    html_content = re.sub(r'^```(?:html)?\n?', '', html_content,
                          flags=re.MULTILINE)
    html_content = re.sub(r'\n?```\s*$', '', html_content,
                          flags=re.MULTILINE).strip()

    # Derive slug from audio filename
    audio_stem = os.path.splitext(os.path.basename(audio))[0]
    slug = audio_stem

    viz_dir = Path(__file__).parent / "viz"
    viz_dir.mkdir(exist_ok=True)
    viz_path = viz_dir / f"{slug}.html"
    viz_path.write_text(html_content)

    print(f"{_c('32', '[Viz]')} Saved: {viz_path}", file=sys.stderr)
    return viz_path


def update_episode_viz_link(episode_id, viz_slug):
    """Append a viz link to the episode description in the DB.

    Idempotent: skips if the viz URL is already present.

    Args:
        episode_id: The podcast episode ID.
        viz_slug: The viz filename slug (without .html extension).
    """
    viz_url = f"https://podcast.do-not-panic.com/viz/{viz_slug}.html"

    conn = get_connection()
    init_db(conn)
    row = conn.execute(
        "SELECT description FROM podcasts WHERE id = ?", (episode_id,)
    ).fetchone()
    if not row:
        conn.close()
        return

    desc = row["description"] or ""
    if viz_url in desc:
        conn.close()
        return

    # Build the anchor-format link
    link_text = f'Interactive Visualization: <a href="{viz_url}">{viz_slug}</a>'
    desc = desc.strip() + f"\n\n{link_text}"
    update_podcast(conn, episode_id, description=desc.strip())
    conn.close()
    print(f"{_c('35', '[Viz]')} Updated episode {episode_id} description "
          f"with viz link", file=sys.stderr)


def upload_viz(viz_path):
    """Upload a viz HTML file to R2 under viz/.

    Args:
        viz_path: Path to the local HTML file.

    Returns:
        Public URL of the uploaded viz.
    """
    from r2_upload import get_r2_client, upload_file

    r2 = get_r2_client()
    filename = os.path.basename(viz_path)
    r2_key = f"viz/{filename}"
    url = upload_file(r2, str(viz_path), r2_key, content_type="text/html")
    return url
