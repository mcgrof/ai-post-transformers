"""RSS 2.0 feed generator and index page for podcast distribution."""

import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from db import get_connection, init_db, list_podcasts

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _c(code, text):
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return text
    return f"\033[{code}m{text}\033[0m"


def _truncate_sentence(text, max_len):
    """Truncate text at the last sentence boundary within max_len."""
    if len(text) <= max_len:
        return text
    chunk = text[:max_len]
    last_period = chunk.rfind(".")
    if last_period > 0:
        return chunk[:last_period + 1]
    return chunk.rstrip() + "..."


def _parse_publish_date(date_str):
    """Convert YYYY-MM-DD to RFC 2822 format for <pubDate>.

    Args:
        date_str: Date string in YYYY-MM-DD format.

    Returns:
        RFC 2822 formatted date string.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def _audio_file_size(audio_file):
    """Get file size in bytes, returning 0 if the file doesn't exist.

    Args:
        audio_file: Path to the audio file.

    Returns:
        File size in bytes, or 0 if missing.
    """
    try:
        return os.path.getsize(audio_file)
    except OSError:
        return 0


def _build_channel(config):
    """Build the RSS <channel> element with show metadata and iTunes tags.

    Args:
        config: Full application config dict (must contain 'spotify' section).

    Returns:
        Tuple of (rss element, channel element).
    """
    spotify = config.get("spotify", {})
    show = spotify.get("show", {})

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = show.get("title", "Podcast")
    ET.SubElement(channel, "description").text = show.get("description", "").strip()
    ET.SubElement(channel, "language").text = show.get("language", "en")
    ET.SubElement(channel, "link").text = show.get("link", "")
    ET.SubElement(channel, "{%s}author" % ITUNES_NS).text = show.get("author", "")
    ET.SubElement(channel, "{%s}explicit" % ITUNES_NS).text = show.get("explicit", "no")

    email = show.get("email", "")
    author = show.get("author", "")
    if email or author:
        owner = ET.SubElement(channel, "{%s}owner" % ITUNES_NS)
        ET.SubElement(owner, "{%s}name" % ITUNES_NS).text = author
        ET.SubElement(owner, "{%s}email" % ITUNES_NS).text = email

    category = show.get("category", "")
    if category:
        ET.SubElement(channel, "{%s}category" % ITUNES_NS, text=category)

    image_url = show.get("image_url", "")
    if image_url:
        ET.SubElement(channel, "{%s}image" % ITUNES_NS, href=image_url)

    return rss, channel


def _episode_url(audio_base_url, filename, nested=False):
    """Build a public URL for an episode file.

    Args:
        audio_base_url: Base URL (e.g. https://...com/episodes).
        filename: Basename of the file.
        nested: If True, use YYYY/MM/{filename} layout. Otherwise flat.
    """
    if nested:
        m = re.match(r"(\d{4})-(\d{2})-\d{2}", filename)
        if m:
            return f"{audio_base_url.rstrip('/')}/{m.group(1)}/{m.group(2)}/{filename}"
    return f"{audio_base_url.rstrip('/')}/{filename}"


def _add_episode(channel, episode, audio_base_url):
    """Add an <item> element for a podcast episode.

    Args:
        channel: The <channel> XML element to append to.
        episode: Dict from list_podcasts() with keys like title, publish_date,
                 elevenlabs_project_id, audio_file, source_urls, paper_ids.
        audio_base_url: URL prefix for audio file hosting.
    """
    item = ET.SubElement(channel, "item")

    ET.SubElement(item, "title").text = episode.get("title", "Untitled")

    # Use stored description if available, fall back to legacy logic
    description = episode.get("description", "")
    if not description:
        desc_parts = []
        source_urls = episode.get("source_urls")
        if source_urls:
            try:
                urls = json.loads(source_urls)
                for url in urls:
                    desc_parts.append(f"Source: {url}")
            except (json.JSONDecodeError, TypeError):
                pass
        paper_ids = episode.get("paper_ids")
        if paper_ids:
            for aid in paper_ids.split(","):
                aid = aid.strip()
                if aid:
                    desc_parts.append(f"Paper: https://arxiv.org/abs/{aid}")
        description = "\n".join(desc_parts)
    ET.SubElement(item, "description").text = description

    # GUID: use audio filename as unique identifier (elevenlabs_project_id is often generic)
    import hashlib
    audio_file = episode.get("audio_file", "")
    ep_id = str(episode.get("id", ""))
    # Generate a stable UUID-like GUID from audio filename or episode ID
    guid_source = audio_file if audio_file else ep_id
    guid_hash = hashlib.sha256(guid_source.encode()).hexdigest()[:32]
    guid_text = f"{guid_hash[:8]}-{guid_hash[8:12]}-{guid_hash[12:16]}-{guid_hash[16:20]}-{guid_hash[20:32]}"
    guid = ET.SubElement(item, "guid", isPermaLink="false")
    guid.text = guid_text

    # Publish date
    pub_date = episode.get("publish_date", "")
    if pub_date:
        ET.SubElement(item, "pubDate").text = _parse_publish_date(pub_date)

    audio_file = episode.get("audio_file", "")

    # Enclosure (audio file)
    if audio_file:
        filename = os.path.basename(audio_file)
        if audio_base_url:
            url = _episode_url(audio_base_url, filename, nested=False)
        else:
            url = filename
        file_size = _audio_file_size(audio_file)
        ET.SubElement(item, "enclosure", {
            "url": url,
            "length": str(file_size),
            "type": "audio/mpeg",
        })

    # Episode cover image
    image_file = episode.get("image_file")
    if image_file:
        image_filename = os.path.basename(image_file)
        if audio_base_url:
            image_url = _episode_url(audio_base_url, image_filename,
                                     nested=False)
        else:
            image_url = image_filename
        ET.SubElement(item, "{%s}image" % ITUNES_NS, href=image_url)

    # Transcript (SRT file — Podcast Index namespace)
    if audio_file:
        srt_path = os.path.splitext(audio_file)[0] + ".srt"
        if os.path.exists(srt_path):
            srt_filename = os.path.basename(srt_path)
            if audio_base_url:
                srt_url = _episode_url(audio_base_url, srt_filename,
                                       nested=False)
            else:
                srt_url = srt_filename
            PODCAST_NS = "https://podcastindex.org/namespace/1.0"
            ET.SubElement(item, "{%s}transcript" % PODCAST_NS, {
                "url": srt_url,
                "type": "application/srt",
                "language": "en",
            })


def _load_anchor_items(config):
    """Load legacy episodes from the cached Anchor RSS feed.

    Returns a list of XML <item> elements from the old feed, excluding
    any episodes that exist in the local DB (matched by title).

    Args:
        config: Full application config dict.
    """
    anchor_feed = Path(__file__).parent / "podcasts" / "anchor_feed.xml"
    if not anchor_feed.exists():
        # Try to download it
        anchor_url = config.get("spotify", {}).get("anchor_rss", "")
        if anchor_url:
            import urllib.request
            print(f"[RSS] Downloading legacy feed from {anchor_url}...", file=sys.stderr)
            urllib.request.urlretrieve(anchor_url, str(anchor_feed))
        else:
            return []

    try:
        tree = ET.parse(str(anchor_feed))
        return tree.findall(".//item")
    except ET.ParseError as e:
        print(f"[RSS] Warning: Failed to parse anchor feed: {e}", file=sys.stderr)
        return []


def generate_feed(config):
    """Generate an RSS 2.0 podcast feed merging legacy Anchor episodes with new ones.

    Legacy episodes keep their original Anchor-hosted audio URLs.
    New episodes (from local DB) use R2-hosted URLs.

    Args:
        config: Full application config dict.
    """
    spotify = config.get("spotify", {})
    audio_base_url = spotify.get("audio_base_url", "")
    feed_file = spotify.get("feed_file", "podcasts/feed.xml")

    if not audio_base_url:
        print("[RSS] Warning: spotify.audio_base_url is empty — enclosure URLs "
              "will be relative filenames only.", file=sys.stderr)

    # Register namespaces so output uses proper prefixes
    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("podcast", "https://podcastindex.org/namespace/1.0")
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
    ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")

    # Build channel
    rss, channel = _build_channel(config)

    # Load legacy Anchor episodes
    anchor_items = _load_anchor_items(config)

    # Get local DB episode titles to avoid duplicates
    conn = get_connection()
    init_db(conn)
    local_episodes = list_podcasts(conn)
    conn.close()

    # Filter out draft episodes (audio still in drafts/, not yet published)
    published_episodes = [
        ep for ep in local_episodes
        if "/drafts/" not in (ep.get("audio_file") or "")
    ]

    # Build set of titles from published episodes that should replace Anchor versions
    local_titles = set()
    for ep in published_episodes:
        title = ep.get("title", "").replace("Episode: ", "")
        local_titles.add(title.strip().lower())

    # Add published local episodes first (newest at top)
    new_count = 0
    for ep in published_episodes:
        _add_episode(channel, ep, audio_base_url)
        new_count += 1

    # Build set of Anchor GUIDs to exclude (replaced by new pipeline)
    replaced_guids = set(
        spotify.get("replaced_anchor_guids", [])
    )

    # Image overrides for legacy episodes (title substring -> R2 URL)
    image_overrides = spotify.get("image_overrides", {})

    # Then add legacy Anchor episodes (skipping replaced ones)
    legacy_count = 0
    for item in anchor_items:
        # Skip by GUID if explicitly replaced
        guid_elem = item.find("guid")
        guid = guid_elem.text.strip() if guid_elem is not None and guid_elem.text else ""
        if guid in replaced_guids:
            continue
        # Skip by title match
        title_elem = item.find("title")
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
        if title.lower() in local_titles:
            continue
        # Apply image overrides
        for match_str, new_url in image_overrides.items():
            if match_str.lower() in title.lower():
                img_el = item.find("{%s}image" % ITUNES_NS)
                if img_el is not None:
                    img_el.set("href", new_url)
                else:
                    ET.SubElement(item, "{%s}image" % ITUNES_NS,
                                  href=new_url)
                break
        channel.append(item)
        legacy_count += 1

    # Write feed
    feed_path = Path(__file__).parent / feed_file
    feed_path.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(str(feed_path), xml_declaration=True, encoding="UTF-8")

    total = new_count + legacy_count
    print(f"{_c('36', '[RSS]')} Feed written to {_c('2', str(feed_path))} "
          f"({_c('32', str(new_count))} new + {legacy_count} legacy = "
          f"{_c('1', str(total))} total episodes)",
          file=sys.stderr)

    # Regenerate index.html alongside feed.xml
    generate_index(config, feed_path)

    return str(feed_path)


def _extract_episodes_from_feed(feed_path):
    """Parse a generated feed.xml and return episode dicts for HTML rendering.

    Returns list of dicts with keys: title, date, description, audio_url,
    image_url, srt_url.
    """
    tree = ET.parse(str(feed_path))
    items = tree.findall(".//item")
    episodes = []
    for item in items:
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        title = re.sub(r"^Episode:\s*", "", title)

        desc_el = item.find("description")
        raw_desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        # Strip HTML tags (legacy Anchor episodes have <p> etc.)
        raw_desc = re.sub(r"<br\s*/?>", "\n", raw_desc)
        raw_desc = re.sub(r"</p>", "\n", raw_desc)
        raw_desc = re.sub(r"<[^>]+>", "", raw_desc)
        raw_desc = raw_desc.strip()

        # Split body from Sources section
        sources_html = ""
        for marker in ["\nSources:", "\n\nSources:"]:
            idx = raw_desc.find(marker)
            if idx > 0:
                sources_raw = raw_desc[idx:].strip()
                raw_desc = raw_desc[:idx].strip()
                # Format sources: escape, convert URLs to links, one per line
                sources_esc = html.escape(sources_raw)
                sources_esc = re.sub(
                    r'(https?://[^\s,)]+)',
                    r'<a href="\1" target="_blank">\1</a>',
                    sources_esc)
                sources_lines = sources_esc.split("\n")
                sources_html = "<br>".join(
                    line for line in sources_lines if line.strip())
                break

        # Clean up body: collapse runs of whitespace within paragraphs
        body_lines = raw_desc.split("\n")
        body_paras = []
        for line in body_lines:
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                body_paras.append(line)
        desc = html.escape(" ".join(body_paras))
        desc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", desc)
        # Convert bare URLs to clickable links in body text
        def _make_link(m):
            escaped_url = m.group(1)
            raw_url = escaped_url.replace("&amp;", "&")
            return f'<a href="{raw_url}" target="_blank">{escaped_url}</a>'
        desc = re.sub(
            r'(https?://[^\s,)<]+)',
            _make_link, desc)

        if sources_html:
            desc = desc + '<div class="card-sources">' + sources_html + "</div>"

        pub_el = item.find("pubDate")
        pub = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
        date_dt = None
        try:
            # Handle GMT suffix (not parsed by %z)
            pub_norm = pub.replace(" GMT", " +0000")
            date_dt = datetime.strptime(pub_norm, "%a, %d %b %Y %H:%M:%S %z")
            date_str = date_dt.strftime("%b %-d, %Y")
        except (ValueError, TypeError):
            date_str = pub

        enc = item.find("enclosure")
        audio = enc.get("url", "") if enc is not None else ""

        img_el = item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}image")
        img = img_el.get("href", "") if img_el is not None else ""

        srt_el = item.find("{https://podcastindex.org/namespace/1.0}transcript")
        srt = srt_el.get("url", "") if srt_el is not None else ""

        episodes.append({
            "title": title,
            "date": date_str,
            "date_dt": date_dt,
            "description": desc,
            "audio_url": audio,
            "image_url": img,
            "srt_url": srt,
        })
    return episodes


def _extract_viz_links(desc):
    """Extract Interactive Visualization links from description HTML."""
    viz = []
    for m in re.finditer(
            r'Interactive Visualization:\s*([^<\n]+)'
            r'.*?href="([^"]+)"', desc):
        viz.append((m.group(1).strip(), m.group(2)))
    return viz


def _render_card(ep, root_prefix=""):
    """Render a single episode card HTML. root_prefix adjusts relative paths."""
    img_html = ""
    if ep["image_url"]:
        img_html = (
            f'<img class="card-img" src="{html.escape(ep["image_url"])}" '
            f'alt="" loading="lazy">')
    else:
        img_html = '<div class="card-img card-img-placeholder"></div>'

    links = []
    if ep["audio_url"]:
        links.append(
            f'<a href="{html.escape(ep["audio_url"])}" download>'
            f'Download</a>')
    if ep["srt_url"]:
        links.append(
            f'<a href="{html.escape(ep["srt_url"])}">Subtitles</a>')
    links_html = " ".join(links)

    audio_html = ""
    if ep["audio_url"]:
        audio_html = (
            f'<audio controls preload="none">'
            f'<source src="{html.escape(ep["audio_url"])}" '
            f'type="audio/mpeg"></audio>')

    # Viz links shown below the date on the card tile
    viz_links = _extract_viz_links(ep["description"])
    viz_html = ""
    if viz_links:
        parts = []
        for title, url in viz_links:
            parts.append(
                f'<a class="card-viz" href="{html.escape(url)}" '
                f'target="_blank">{html.escape(title)}</a>')
        viz_html = "\n    ".join(parts)

    return f"""<div class="card">
  <div class="card-visual">
    {img_html}
  </div>
  <div class="card-meta">
    <div class="card-title">{html.escape(ep["title"])}</div>
    <div class="card-date">{html.escape(ep["date"])}</div>
    {viz_html}
  </div>
  <div class="card-body">
    <p class="card-desc">{ep["description"]}</p>
    {audio_html}
    <div class="card-links">{links_html}</div>
  </div>
</div>"""


def generate_index(config, feed_path=None):
    """Generate index.html (latest 8 episodes) and per-month archive pages."""
    import calendar
    from collections import defaultdict

    spotify = config.get("spotify", {})
    show = spotify.get("show", {})
    show_title = show.get("title", "AI Post Transformers")
    show_desc = show.get("description", "")
    show_image = show.get("image_url", "")
    anchor_rss = spotify.get("anchor_rss", "")
    github_repo = config.get("github", {}).get("repo", "")

    if feed_path is None:
        feed_path = Path(__file__).parent / spotify.get("feed_file", "podcasts/feed.xml")
    feed_path = Path(feed_path)

    if not feed_path.exists():
        print("[HTML] Skipping index.html: feed.xml not found", file=sys.stderr)
        return None

    episodes = _extract_episodes_from_feed(feed_path)
    total = len(episodes)

    # Latest 8 for index
    latest = episodes[:8]
    cards_html = "\n".join(_render_card(ep) for ep in latest)

    # Group all episodes by year/month for archive
    by_month = defaultdict(list)
    for ep in episodes:
        dt = ep.get("date_dt")
        if dt:
            by_month[(dt.year, dt.month)].append(ep)
        else:
            by_month[(0, 0)].append(ep)

    # Build archive links sorted newest first
    sorted_months = sorted(
        ((ym, eps) for ym, eps in by_month.items() if ym != (0, 0)),
        key=lambda x: x[0], reverse=True)
    archive_links = []
    for (year, month), month_eps in sorted_months:
        label = f"{calendar.month_name[month]} {year}"
        href = f"{year}/{month:02d}/index.html"
        archive_links.append(
            f'<a class="archive-link" href="{href}">'
            f'{html.escape(label)} '
            f'<span class="ep-count">({len(month_eps)})</span></a>')
    archive_html = "\n    ".join(archive_links)

    count = total

    # Build search index: all episodes with title, date, archive page link
    search_index = []
    for ep in episodes:
        dt = ep.get("date_dt")
        month_href = ""
        if dt:
            month_href = f"{dt.year}/{dt.month:02d}/index.html"
        search_index.append({
            "t": ep["title"],
            "d": ep["date"],
            "m": month_href,
            "a": ep.get("audio_url", ""),
        })
    search_json = json.dumps(search_index, separators=(",", ":"))

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(show_title)}</title>
<meta name="description" content="{html.escape(show_desc[:200])}">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
  background: #141414;
  color: #e5e5e5;
  line-height: 1.5;
  min-height: 100vh;
}}
a {{ color: #e50914; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* --- Hero --- */
.hero {{
  position: relative;
  padding: 4rem 2rem 3rem;
  text-align: center;
  background: url("images/podcast-bg.png") center bottom / cover no-repeat,
              linear-gradient(180deg, #1a1a2e 0%, #141414 100%);
  overflow: hidden;
}}
.hero::before {{
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(20,20,20,0.45) 0%, #141414 100%),
              radial-gradient(ellipse at 50% 0%, rgba(229,9,20,0.12) 0%, transparent 70%);
  pointer-events: none;
}}
.hero-art {{
  width: 220px; height: 220px;
  border-radius: 16px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.6);
  margin-bottom: 1.5rem;
  position: relative;
}}
.hero p {{
  max-width: 580px;
  margin: 0 auto 1.2rem;
  color: #999;
  font-size: 0.95rem;
  position: relative;
}}
.hero-links {{
  display: flex;
  gap: 1rem;
  justify-content: center;
  flex-wrap: wrap;
  position: relative;
}}
.hero-links a {{
  display: inline-block;
  transition: transform 0.2s, opacity 0.2s;
}}
.hero-links a:hover {{
  transform: scale(1.1);
  opacity: 0.85;
  text-decoration: none;
}}
.hero-links img {{
  height: 180px;
  width: auto;
  border-radius: 8px;
}}

/* --- Grid --- */
.section {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}}
.section-title {{
  font-size: 1.3rem;
  font-weight: 600;
  color: #fff;
  margin-bottom: 1.2rem;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 1.2rem;
}}

/* --- Card: fixed-size tile, overlay on hover --- */
.card {{
  position: relative;
  border-radius: 6px;
  overflow: visible;
  cursor: default;
}}
.card-visual {{
  position: relative;
  aspect-ratio: 1 / 1;
  border-radius: 6px;
  overflow: hidden;
  background: #222;
  transition: transform 0.3s cubic-bezier(.25,.46,.45,.94),
              box-shadow 0.3s ease;
  z-index: 1;
}}
.card:hover .card-visual {{
  transform: scale(1.05);
  box-shadow: 0 14px 36px rgba(0,0,0,0.7);
  z-index: 10;
  border-radius: 6px 6px 0 0;
}}
.card-img {{
  width: 100%; height: 100%;
  object-fit: cover;
  transition: transform 0.4s ease;
}}
.card:hover .card-img {{ transform: scale(1.1); }}
.card-img-placeholder {{
  width: 100%; height: 100%;
  background: linear-gradient(135deg, #1a1a2e, #2d2d44);
}}
.card-meta {{
  position: relative;
  padding: 0.6rem 0.5rem 0.3rem;
  background: #1a1a1a;
  transition: transform 0.3s cubic-bezier(.25,.46,.45,.94);
  z-index: 2;
}}
.card:hover .card-meta {{
  transform: scale(1.05);
  z-index: 11;
}}
.card-title {{
  font-size: 0.85rem;
  font-weight: 600;
  color: #e5e5e5;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}
.card-date {{
  font-size: 0.72rem;
  color: #777;
  margin-top: 0.15rem;
}}
.card-viz {{
  display: block;
  font-size: 0.7rem;
  color: #5eeacd;
  margin-top: 0.25rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.card-viz:hover {{
  color: #fff;
  text-decoration: underline;
}}

/* Expanded body: wider centered overlay below the visual */
.card-body {{
  position: absolute;
  top: 100%;
  left: 50%;
  width: 520px;
  max-width: 90vw;
  background: #1c1c1c;
  border-radius: 0 0 6px 6px;
  padding: 1rem 1.2rem;
  z-index: 9;
  box-shadow: 0 10px 30px rgba(0,0,0,0.6);
  opacity: 0;
  visibility: hidden;
  transform: translate(-50%, -8px);
  transform-origin: top center;
  transition: opacity 0.25s ease, visibility 0.25s ease,
              transform 0.25s cubic-bezier(.25,.46,.45,.94);
  pointer-events: none;
}}
.card:hover .card-body {{
  opacity: 1;
  visibility: visible;
  transform: translate(-50%, 0);
  pointer-events: auto;
}}
.card-desc {{
  font-size: 0.82rem;
  color: #e5e5e5;
  line-height: 1.6;
  margin-bottom: 0.6rem;
}}
.card-sources {{
  margin-top: 0.8rem;
  padding-top: 0.6rem;
  border-top: 1px solid #333;
  font-size: 0.75rem;
  color: #999;
  line-height: 1.7;
}}
.card-sources a {{
  color: #7ab;
  word-break: break-all;
}}
.card-sources a:hover {{ color: #e50914; }}
.card-body audio {{
  width: 100%;
  height: 34px;
  margin-bottom: 0.4rem;
  border-radius: 4px;
}}
.card-links {{
  display: flex;
  gap: 1rem;
  font-size: 0.75rem;
}}
.card-links a {{ color: #999; }}
.card-links a:hover {{ color: #e50914; text-decoration: none; }}

/* Last row: expand upward instead of downward to avoid overflow */
.card.expand-up .card-body {{
  top: auto;
  bottom: 100%;
  border-radius: 6px 6px 0 0;
  transform-origin: bottom center;
  transform: translate(-50%, 8px);
}}
.card.expand-up:hover .card-body {{
  transform: translate(-50%, 0);
}}
.card.expand-up:hover .card-visual {{
  border-radius: 0 0 6px 6px;
}}

/* --- Search --- */
.search-section {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 1.5rem 1.5rem 0;
}}
.search-box {{
  width: 100%;
  padding: 0.7rem 1rem 0.7rem 2.6rem;
  background: #1a1a1a;
  border: 1px solid #333;
  border-radius: 8px;
  color: #e5e5e5;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.2s;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='%23777' viewBox='0 0 16 16'%3E%3Cpath d='M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85zm-5.242.156a5 5 0 1 1 0-10 5 5 0 0 1 0 10z'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: 0.8rem center;
}}
.search-box:focus {{
  border-color: #e50914;
}}
.search-box::placeholder {{
  color: #666;
}}
.search-results {{
  margin-top: 0.8rem;
  display: none;
}}
.search-results.active {{
  display: block;
}}
.search-result {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.5rem 0.6rem;
  border-radius: 4px;
  transition: background 0.15s;
}}
.search-result:hover {{
  background: #1a1a1a;
}}
.search-result-title {{
  font-size: 0.85rem;
  color: #e5e5e5;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.search-result-title a {{
  color: #e5e5e5;
}}
.search-result-title a:hover {{
  color: #e50914;
}}
.search-result-date {{
  font-size: 0.72rem;
  color: #777;
  margin-left: 1rem;
  white-space: nowrap;
}}
.search-count {{
  font-size: 0.78rem;
  color: #666;
  margin-bottom: 0.5rem;
}}

/* --- Archive --- */
.archive-section {{
  border-top: 1px solid #222;
}}
.archive-grid {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
}}
.archive-link {{
  display: inline-block;
  padding: 0.4rem 0.9rem;
  background: #1a1a1a;
  border-radius: 6px;
  font-size: 0.85rem;
  color: #ccc;
  transition: background 0.2s;
}}
.archive-link:hover {{
  background: #2a2a2a;
  text-decoration: none;
  color: #fff;
}}
.ep-count {{
  color: #666;
  font-size: 0.75rem;
}}

/* --- Suggest link --- */
.suggest-link {{
  display: inline-block;
  margin-top: 0.8rem;
  font-size: 0.85rem;
  color: #777;
  position: relative;
}}
.suggest-link:hover {{ color: #e50914; }}

/* --- Footer --- */
.footer {{
  text-align: center;
  padding: 2rem;
  color: #555;
  font-size: 0.78rem;
  border-top: 1px solid #222;
  margin-top: 0;
}}

@media (max-width: 600px) {{
  .hero {{ padding: 2.5rem 1rem 2rem; }}
  .hero-art {{ width: 160px; height: 160px; }}
  .hero-links img {{ height: 120px; }}
  .grid {{
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.8rem;
  }}
  /* Mobile: card body hidden by default, shown on tap via .active */
  .card-body {{
    position: fixed;
    top: auto;
    bottom: 0;
    left: 0;
    width: 100%;
    max-width: 100vw;
    max-height: 70vh;
    overflow-y: auto;
    border-radius: 12px 12px 0 0;
    transform: translateY(100%);
    opacity: 0;
    visibility: hidden;
    z-index: 1000;
    pointer-events: none;
    transition: transform 0.3s ease, opacity 0.25s ease,
                visibility 0.25s ease;
  }}
  .card.active .card-body {{
    transform: translateY(0);
    opacity: 1;
    visibility: visible;
    pointer-events: auto;
  }}
  .card-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.5);
    z-index: 999;
  }}
  .card.active + .card-overlay,
  .card-overlay.active {{
    display: block;
  }}
  .card-visual {{ border-radius: 6px; }}
  .card:hover .card-visual {{ transform: none; box-shadow: none;
    border-radius: 6px; }}
  .card:hover .card-meta {{ transform: none; }}
  .card:hover .card-body {{ transform: translateY(100%);
    opacity: 0; visibility: hidden; }}
  .card.active:hover .card-body {{ transform: translateY(0);
    opacity: 1; visibility: visible; }}
}}
</style>
</head>
<body>

<div class="hero">
  <img class="hero-art" src="{html.escape(show_image)}" alt="{html.escape(show_title)}" width="220" height="220">
  <p>{html.escape(_truncate_sentence(show_desc, 200))}</p>
  <div class="hero-links">
    <a href="{html.escape(anchor_rss)}" title="Spotify"><img src="images/spotify.png" alt="Spotify"></a>
    <a href="https://podcasts.apple.com/us/podcast/ai-post-transformers/id1835878324" title="Apple Podcasts"><img src="images/apple-podcasts.png" alt="Apple Podcasts"></a>
    <a href="https://music.amazon.com/podcasts/bad34c42-bf59-4b9a-8085-5742fd63e011/ai-post-transformers" title="Amazon Podcasts"><img src="images/amazon-podcasts.png" alt="Amazon Podcasts"></a>
    <a href="feed.xml" title="RSS Feed"><img src="images/rss-feed.png" alt="RSS Feed"></a>
    <a href="queue.html" title="Paper Queue"><img src="images/paper-queue.png" alt="Paper Queue"></a>
    <a href="sister-podcasts.html" title="Sister Podcasts"><img src="images/sister-podcasts.png" alt="Sister Podcasts"></a>
{"" if not github_repo else f'    <a href="https://github.com/{html.escape(github_repo)}" title="GitHub"><img src="images/github.png" alt="GitHub"></a>'}
  </div>
{"" if not github_repo else f'  <a class="suggest-link" href="https://github.com/{html.escape(github_repo)}/issues/new?template=paper-submission.yml" target="_blank">Know a paper we should cover? Suggest it &rarr;</a>'}
</div>

<div class="search-section">
  <input class="search-box" type="text" id="ep-search"
         placeholder="Search {count} episodes..." autocomplete="off">
  <div class="search-results" id="search-results"></div>
</div>

<div class="section" id="latest-section">
  <div class="section-title">Latest Episodes</div>
  <div class="grid">
{cards_html}
  </div>
</div>

<div class="section archive-section">
  <div class="section-title">Archive ({count} episodes)</div>
  <div class="archive-grid">
    {archive_html}
  </div>
</div>

<div class="footer">
  {html.escape(show_title)}
</div>

<div class="card-overlay" id="card-overlay"></div>

<script>
(function() {{
  var cards = document.querySelectorAll('.card');
  if (!cards.length) return;

  // Desktop: tag last-row cards to expand upward
  var lastTop = cards[cards.length - 1].getBoundingClientRect().top;
  for (var i = cards.length - 1; i >= 0; i--) {{
    if (Math.abs(cards[i].getBoundingClientRect().top - lastTop) < 5)
      cards[i].classList.add('expand-up');
    else break;
  }}

  // Mobile: tap to toggle card detail as bottom sheet
  var isMobile = window.matchMedia('(max-width: 600px)');
  var overlay = document.getElementById('card-overlay');

  function closeActive() {{
    var active = document.querySelector('.card.active');
    if (active) active.classList.remove('active');
    overlay.classList.remove('active');
  }}

  cards.forEach(function(card) {{
    card.addEventListener('click', function(e) {{
      if (!isMobile.matches) return;
      // Don't intercept clicks on links/audio controls
      if (e.target.closest('a, audio, button')) return;
      e.stopPropagation();
      var wasActive = card.classList.contains('active');
      closeActive();
      if (!wasActive) {{
        card.classList.add('active');
        overlay.classList.add('active');
      }}
    }});
  }});

  overlay.addEventListener('click', closeActive);
}})();
</script>

<script>
(function() {{
  var idx = {search_json};
  var box = document.getElementById('ep-search');
  var results = document.getElementById('search-results');
  var latest = document.getElementById('latest-section');
  var archive = document.querySelector('.archive-section');

  box.addEventListener('input', function() {{
    var q = box.value.trim().toLowerCase();
    if (q.length < 2) {{
      results.classList.remove('active');
      results.innerHTML = '';
      if (latest) latest.style.display = '';
      if (archive) archive.style.display = '';
      return;
    }}
    var terms = q.split(/\\s+/);
    var hits = idx.filter(function(e) {{
      var t = e.t.toLowerCase();
      return terms.every(function(w) {{ return t.indexOf(w) >= 0; }});
    }});
    if (latest) latest.style.display = 'none';
    if (archive) archive.style.display = 'none';
    if (!hits.length) {{
      results.innerHTML = '<div class="search-count">No episodes found</div>';
      results.classList.add('active');
      return;
    }}
    var html = '<div class="search-count">' + hits.length + ' episode' +
               (hits.length === 1 ? '' : 's') + ' found</div>';
    hits.forEach(function(e) {{
      var href = e.m || '#';
      html += '<div class="search-result">' +
              '<span class="search-result-title">' +
              '<a href="' + href + '">' +
              e.t.replace(/</g, '&lt;') + '</a></span>' +
              '<span class="search-result-date">' + e.d + '</span></div>';
    }});
    results.innerHTML = html;
    results.classList.add('active');
  }});
}})();
</script>

</body>
</html>
"""

    index_path = feed_path.parent / "index.html"
    index_path.write_text(page)
    print(f"{_c('34', '[HTML]')} Index written to {_c('2', str(index_path))} "
          f"({_c('1', str(count))} episodes)",
          file=sys.stderr)

    # Generate per-month archive pages
    _generate_month_pages(sorted_months, show_title, feed_path.parent)

    # Generate about page alongside index
    generate_about(config, feed_path.parent)

    return str(index_path)


def _generate_month_pages(sorted_months, show_title, output_dir):
    """Generate per-month archive pages under YYYY/MM/index.html."""
    import calendar

    for (year, month), month_eps in sorted_months:
        label = f"{calendar.month_name[month]} {year}"
        cards_html = "\n".join(_render_card(ep, root_prefix="../../") for ep in month_eps)

        page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(label)} &mdash; {html.escape(show_title)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
  background: #141414;
  color: #e5e5e5;
  line-height: 1.5;
  min-height: 100vh;
}}
a {{ color: #e50914; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.page-header {{
  padding: 2rem 1.5rem 1rem;
  max-width: 1200px;
  margin: 0 auto;
}}
.back {{ font-size: 0.85rem; color: #777; }}
.back:hover {{ color: #e5e5e5; }}
h1 {{
  font-size: 1.4rem;
  font-weight: 600;
  color: #fff;
  margin-top: 0.8rem;
}}
.section {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem 1.5rem 2rem;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 1.2rem;
}}
.card {{
  position: relative;
  border-radius: 6px;
  overflow: visible;
  cursor: default;
}}
.card-visual {{
  position: relative;
  aspect-ratio: 1 / 1;
  border-radius: 6px;
  overflow: hidden;
  background: #222;
  transition: transform 0.3s cubic-bezier(.25,.46,.45,.94),
              box-shadow 0.3s ease;
  z-index: 1;
}}
.card:hover .card-visual {{
  transform: scale(1.05);
  box-shadow: 0 14px 36px rgba(0,0,0,0.7);
  z-index: 10;
  border-radius: 6px 6px 0 0;
}}
.card-img {{
  width: 100%; height: 100%;
  object-fit: cover;
  transition: transform 0.4s ease;
}}
.card:hover .card-img {{ transform: scale(1.1); }}
.card-img-placeholder {{
  width: 100%; height: 100%;
  background: linear-gradient(135deg, #1a1a2e, #2d2d44);
}}
.card-meta {{
  position: relative;
  padding: 0.6rem 0.5rem 0.3rem;
  background: #1a1a1a;
  transition: transform 0.3s cubic-bezier(.25,.46,.45,.94);
  z-index: 2;
}}
.card:hover .card-meta {{
  transform: scale(1.05);
  z-index: 11;
}}
.card-title {{
  font-size: 0.85rem;
  font-weight: 600;
  color: #e5e5e5;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}
.card-date {{
  font-size: 0.72rem;
  color: #777;
  margin-top: 0.15rem;
}}
.card-viz {{
  display: block;
  font-size: 0.7rem;
  color: #5eeacd;
  margin-top: 0.25rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.card-viz:hover {{
  color: #fff;
  text-decoration: underline;
}}
.card-body {{
  position: absolute;
  top: 100%;
  left: 50%;
  width: 520px;
  max-width: 90vw;
  background: #1c1c1c;
  border-radius: 0 0 6px 6px;
  padding: 1rem 1.2rem;
  z-index: 9;
  box-shadow: 0 10px 30px rgba(0,0,0,0.6);
  opacity: 0;
  visibility: hidden;
  transform: translate(-50%, -8px);
  transform-origin: top center;
  transition: opacity 0.25s ease, visibility 0.25s ease,
              transform 0.25s cubic-bezier(.25,.46,.45,.94);
  pointer-events: none;
}}
.card:hover .card-body {{
  opacity: 1;
  visibility: visible;
  transform: translate(-50%, 0);
  pointer-events: auto;
}}
.card-desc {{
  font-size: 0.82rem;
  color: #e5e5e5;
  line-height: 1.6;
  margin-bottom: 0.6rem;
}}
.card-sources {{
  margin-top: 0.8rem;
  padding-top: 0.6rem;
  border-top: 1px solid #333;
  font-size: 0.75rem;
  color: #999;
  line-height: 1.7;
}}
.card-sources a {{
  color: #7ab;
  word-break: break-all;
}}
.card-sources a:hover {{ color: #e50914; }}
.card-body audio {{
  width: 100%;
  height: 34px;
  margin-bottom: 0.4rem;
  border-radius: 4px;
}}
.card-links {{
  display: flex;
  gap: 1rem;
  font-size: 0.75rem;
}}
.card-links a {{ color: #999; }}
.card-links a:hover {{ color: #e50914; text-decoration: none; }}
.card.expand-up .card-body {{
  top: auto;
  bottom: 100%;
  border-radius: 6px 6px 0 0;
  transform-origin: bottom center;
  transform: translate(-50%, 8px);
}}
.card.expand-up:hover .card-body {{
  transform: translate(-50%, 0);
}}
.card.expand-up:hover .card-visual {{
  border-radius: 0 0 6px 6px;
}}
.footer {{
  text-align: center;
  padding: 2rem;
  color: #555;
  font-size: 0.78rem;
  border-top: 1px solid #222;
  margin-top: 0;
}}
@media (max-width: 600px) {{
  .grid {{
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.8rem;
  }}
  .card-body {{
    position: fixed;
    top: auto; bottom: 0; left: 0;
    width: 100%; max-width: 100vw; max-height: 70vh;
    overflow-y: auto;
    border-radius: 12px 12px 0 0;
    transform: translateY(100%);
    opacity: 0; visibility: hidden;
    z-index: 1000; pointer-events: none;
    transition: transform 0.3s ease, opacity 0.25s ease, visibility 0.25s ease;
  }}
  .card.active .card-body {{
    transform: translateY(0);
    opacity: 1; visibility: visible; pointer-events: auto;
  }}
  .card-overlay {{
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.5); z-index: 999;
  }}
  .card.active + .card-overlay, .card-overlay.active {{ display: block; }}
  .card-visual {{ border-radius: 6px; }}
  .card:hover .card-visual {{ transform: none; box-shadow: none; border-radius: 6px; }}
  .card:hover .card-meta {{ transform: none; }}
  .card:hover .card-body {{ transform: translateY(100%); opacity: 0; visibility: hidden; }}
  .card.active:hover .card-body {{ transform: translateY(0); opacity: 1; visibility: visible; }}
}}
</style>
</head>
<body>

<div class="page-header">
  <a class="back" href="../../index.html">&larr; All episodes</a>
  <h1>{html.escape(label)}</h1>
</div>

<div class="section">
  <div class="grid">
{cards_html}
  </div>
</div>

<div class="footer">
  {html.escape(show_title)} &middot; <a href="../../index.html">Home</a>
</div>

<div class="card-overlay" id="card-overlay"></div>

<script>
(function() {{
  var cards = document.querySelectorAll('.card');
  if (!cards.length) return;
  var lastTop = cards[cards.length - 1].getBoundingClientRect().top;
  for (var i = cards.length - 1; i >= 0; i--) {{
    if (Math.abs(cards[i].getBoundingClientRect().top - lastTop) < 5)
      cards[i].classList.add('expand-up');
    else break;
  }}
  var isMobile = window.matchMedia('(max-width: 600px)');
  var overlay = document.getElementById('card-overlay');
  function closeActive() {{
    var a = document.querySelector('.card.active');
    if (a) a.classList.remove('active');
    overlay.classList.remove('active');
  }}
  cards.forEach(function(c) {{
    c.addEventListener('click', function(e) {{
      if (!isMobile.matches) return;
      if (e.target.closest('a, audio, button')) return;
      e.stopPropagation();
      var w = c.classList.contains('active');
      closeActive();
      if (!w) {{ c.classList.add('active'); overlay.classList.add('active'); }}
    }});
  }});
  overlay.addEventListener('click', closeActive);
}})();
</script>

</body>
</html>
"""

        month_dir = Path(output_dir) / str(year) / f"{month:02d}"
        month_dir.mkdir(parents=True, exist_ok=True)
        month_path = month_dir / "index.html"
        month_path.write_text(page)

    count = len(sorted_months)
    if count:
        print(f"{_c('34', '[HTML]')} Generated {_c('1', str(count))} "
              f"archive page(s)", file=sys.stderr)


def generate_about(config, output_dir):
    """Generate about.html with sister podcast listings.

    Sister podcasts are configured in config.yaml under
    sister_podcasts. Each entry has title, description, image,
    spotify_url, and status (complete or active).
    """
    sisters = config.get("sister_podcasts", [])
    if not sisters:
        return None

    spotify = config.get("spotify", {})
    show = spotify.get("show", {})
    show_title = show.get("title", "AI Post Transformers")

    cards = []
    for pod in sisters:
        title = html.escape(pod.get("title", ""))
        desc = html.escape(pod.get("description", "").strip())
        image = html.escape(pod.get("image", ""))
        url = html.escape(pod.get("spotify_url", ""))
        status = pod.get("status", "active")

        badge = ""
        if status == "complete":
            badge = '<span class="badge-complete">Complete Series</span>'

        cards.append(f"""<a class="sister-card" href="{url}" target="_blank">
  <img class="sister-img" src="{image}" alt="{title}" loading="lazy">
  <div class="sister-info">
    <div class="sister-title">{title} {badge}</div>
    <p class="sister-desc">{desc}</p>
  </div>
</a>""")

    cards_html = "\n".join(cards)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sister Podcasts &mdash; {html.escape(show_title)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Helvetica, Arial, sans-serif;
  background: #141414;
  color: #e5e5e5;
  line-height: 1.6;
  min-height: 100vh;
}}
a {{ color: #e50914; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

.page {{
  max-width: 720px;
  margin: 0 auto;
  padding: 3rem 1.5rem 2rem;
}}
.back {{ font-size: 0.85rem; color: #777; margin-bottom: 2rem; display: inline-block; }}
.back:hover {{ color: #e5e5e5; }}
h1 {{
  font-size: 1.6rem;
  font-weight: 700;
  color: #fff;
  margin-bottom: 0.5rem;
}}
.subtitle {{
  color: #777;
  font-size: 0.9rem;
  margin-bottom: 2rem;
}}

.sister-card {{
  display: flex;
  gap: 1.2rem;
  align-items: flex-start;
  padding: 1.2rem;
  border-radius: 8px;
  background: #1a1a1a;
  margin-bottom: 1rem;
  text-decoration: none;
  color: #e5e5e5;
  transition: background 0.2s, transform 0.2s;
}}
.sister-card:hover {{
  background: #222;
  transform: translateY(-2px);
  text-decoration: none;
}}
.sister-img {{
  width: 100px;
  height: 100px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
}}
.sister-info {{
  flex: 1;
  min-width: 0;
}}
.sister-title {{
  font-size: 1rem;
  font-weight: 600;
  color: #fff;
  margin-bottom: 0.3rem;
}}
.sister-desc {{
  font-size: 0.85rem;
  color: #999;
  line-height: 1.5;
}}
.badge-complete {{
  display: inline-block;
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #888;
  background: #2a2a2a;
  border: 1px solid #333;
  padding: 0.15em 0.5em;
  border-radius: 3px;
  vertical-align: middle;
  margin-left: 0.4rem;
}}
.footer {{
  text-align: center;
  padding: 2rem;
  color: #555;
  font-size: 0.78rem;
  border-top: 1px solid #222;
  margin-top: 2rem;
}}

@media (max-width: 480px) {{
  .sister-card {{ flex-direction: column; align-items: center;
    text-align: center; }}
  .sister-img {{ width: 120px; height: 120px; }}
}}
</style>
</head>
<body>

<div class="page">
  <a class="back" href="index.html">&larr; Back to episodes</a>
  <h1>Sister Podcasts</h1>
  <p class="subtitle">Earlier podcast series from the same team.
    These series are complete and are no longer releasing new episodes &mdash;
    {html.escape(show_title)} is now the home for all new publications.
    Every past episode remains available to listen.</p>

{cards_html}

</div>

<div class="footer">
  {html.escape(show_title)}
</div>

</body>
</html>
"""

    out_path = Path(output_dir) / "sister-podcasts.html"
    out_path.write_text(page)
    print(f"{_c('34', '[HTML]')} Sister podcasts written to "
          f"{_c('2', str(out_path))} "
          f"({_c('1', str(len(sisters)))} podcasts)",
          file=sys.stderr)
    return str(out_path)
