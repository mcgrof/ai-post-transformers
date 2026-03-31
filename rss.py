"""RSS 2.0 feed generator and index page for podcast distribution."""

import html
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from db import get_connection, init_db, list_podcasts

# Path to cached legacy episode slugs (generated from R2 listing)
LEGACY_SLUGS_PATH = Path("/tmp/r2_legacy_slugs.json")

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


def _slug_from_title(title):
    """Create a URL-friendly slug from an episode title."""
    slug = title.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug)
    slug = slug.strip('-')
    return slug[:80].rstrip('-') or 'episode'


def _title_from_slug(slug):
    """Convert a URL slug back to a display title.

    Replaces hyphens with spaces and applies title case.
    """
    return slug.replace('-', ' ').title()


def _load_legacy_slugs():
    """Load legacy episode slugs from the cached JSON file.

    Returns a list of slug strings. Returns empty list if file
    doesn't exist or can't be parsed.
    """
    if not LEGACY_SLUGS_PATH.exists():
        return []
    try:
        with open(LEGACY_SLUGS_PATH) as f:
            slugs = json.load(f)
        if isinstance(slugs, list):
            return [s for s in slugs if isinstance(s, str)]
        return []
    except (json.JSONDecodeError, OSError) as e:
        print(f"[RSS] Warning: Could not load legacy slugs: {e}",
              file=sys.stderr)
        return []


def _plain_text_description(desc_html):
    """Convert stored episode description HTML into compact searchable text."""
    text = desc_html or ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text




def _format_sources_html(sources_raw):
    """Render a Sources section into stable HTML even when the feed stored it
    as flattened numbered items, title/url blobs, or raw back-to-back URLs.
    """
    if not sources_raw:
        return ""

    body = re.sub(r'^\s*Sources:\s*', '', sources_raw.strip(), flags=re.I)
    body = html.unescape(body)
    body = re.sub(r'(?<!^)(\d+\.\s)', r'\n\1', body)
    body = re.sub(r'(?<!^)(https?://)', r'\n\1', body)
    body = re.sub(r'[ \t]+', ' ', body)
    body = re.sub(r'\n\s*', '\n', body).strip()

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    rendered = ['Sources:']
    pending_title = None

    def flush_title():
        nonlocal pending_title
        if pending_title:
            rendered.append(html.escape(pending_title.strip()))
            pending_title = None

    for line in lines:
        if re.match(r'^https?://', line):
            flush_title()
            for url in re.findall(r'https?://[^\s,)]+', line):
                esc_url = html.escape(url)
                rendered.append(f'     <a href="{esc_url}" target="_blank">{esc_url}</a>')
            continue

        if re.match(r'^\d+\.\s', line):
            flush_title()
            pending_title = line
            continue

        if pending_title:
            pending_title += ' ' + line
        else:
            rendered.append(html.escape(line))

    flush_title()
    return '<br>'.join(line for line in rendered if line.strip())




def _normalize_description_html(desc_html):
    """Repair malformed stored description HTML before rendering/searching.

    Older episodes may contain a card-sources block with all URLs flattened into
    one giant anchor. Rebuild that block from its visible text so the page style
    stays consistent with modern episodes.
    """
    if not desc_html:
        return ""

    def _fix_block(match):
        inner = match.group(1)
        plain = re.sub(r'<br\s*/?>', '\n', inner)
        plain = re.sub(r'<[^>]+>', '', plain)
        plain = html.unescape(plain).strip()
        fixed = _format_sources_html(plain if plain.lower().startswith('sources:') else 'Sources: ' + plain)
        return '<div class="card-sources">' + fixed + '</div>'

    desc_html = re.sub(r'<div class="card-sources">(.*?)</div>', _fix_block, desc_html, flags=re.S)

    if '<div class="card-sources">' not in desc_html and 'Sources:' in desc_html:
        prefix, suffix = desc_html.split('Sources:', 1)
        suffix_plain = re.sub(r'<br\s*/?>', '\n', suffix)
        suffix_plain = re.sub(r'<[^>]+>', '', suffix_plain)
        suffix_plain = html.unescape(suffix_plain).strip()
        fixed = _format_sources_html('Sources: ' + suffix_plain)
        desc_html = prefix.rstrip() + '<div class="card-sources">' + fixed + '</div>'

    return desc_html




def _search_alias_terms(text):
    """Add search aliases for important cited papers/method names.

    This helps homepage search find episodes by common misspellings, shorthand,
    or canonical paper identifiers even when the stored source URL is a scholar
    query rather than a direct arXiv link.
    """
    hay = (text or '').lower()
    aliases = []

    if 'heavy-hitter oracle' in hay or re.search(r'h2o', hay):
        aliases.extend(['h2o', 'h20', 'heavy-hitter oracle', '2306.14048'])

    return ' '.join(dict.fromkeys(aliases))


def _build_search_index(episodes, legacy_slugs=None):
    """Build a full-catalog search index for homepage search.

    Includes all feed episodes plus any extra legacy slugs that are not already
    represented in the merged feed.
    """
    feed_slugs = set()
    search_idx = []

    for ep in episodes:
        slug = ep.get("slug") or _slug_from_title(ep.get("title", ""))
        feed_slugs.add(slug)
        desc_full = _plain_text_description(_normalize_description_html(ep.get("description", "")))
        desc_plain = _truncate_sentence(desc_full, 240)
        alias_terms = _search_alias_terms((ep.get("title", "") or "") + "\n" + desc_full)
        search_idx.append({
            "t": ep.get("title", ""),
            "d": ep.get("date", ""),
            "s": slug,
            "u": f"episodes/{slug}/",
            "x": desc_plain,
            "q": (desc_full + ' ' + alias_terms).strip(),
            "i": ep.get("thumb_url") or ep.get("image_url", ""),
            "l": False,
        })

    extra_legacy = sorted({s for s in (legacy_slugs or []) if s not in feed_slugs})
    for slug in extra_legacy:
        search_idx.append({
            "t": _title_from_slug(slug),
            "d": "Legacy",
            "s": slug,
            "u": f"episodes/{slug}/",
            "x": "Legacy episode",
            "q": _title_from_slug(slug),
            "i": "",
            "l": True,
        })

    return search_idx


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
    # and private episodes (visibility='private') which must never appear
    # in the public RSS feed.
    published_episodes = [
        ep for ep in local_episodes
        if "/drafts/" not in (ep.get("audio_file") or "")
        and ep.get("visibility", "public") != "private"
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
        # Extract viz links before HTML stripping destroys anchor tags
        viz_links = _extract_viz_links(raw_desc)
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

        # Re-inject viz links as clickable URLs (tag stripping removed them)
        if viz_links:
            # Remove orphaned "Interactive Visualization: Title" text
            desc = re.sub(
                r'\s*Interactive Visualization:\s*[^<]+$', '', desc)
            for viz_title, viz_url in viz_links:
                esc_url = html.escape(viz_url)
                esc_title = html.escape(viz_title)
                desc += (f'<div class="card-viz-desc">'
                         f'Interactive Visualization: '
                         f'<a href="{esc_url}" target="_blank">'
                         f'{esc_title}</a></div>')

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

        # Compute thumbnail URL from image URL
        thumb_url = ""
        if img:
            img_stem = os.path.splitext(
                os.path.basename(urllib.parse.urlparse(img).path))[0]
            if img_stem:
                thumb_url = (
                    f"https://podcast.do-not-panic.com/thumbs/"
                    f"{img_stem}.webp")

        episodes.append({
            "title": title,
            "date": date_str,
            "date_dt": date_dt,
            "description": desc,
            "audio_url": audio,
            "image_url": img,
            "thumb_url": thumb_url,
            "srt_url": srt,
            "viz_links": viz_links,
        })
    return episodes


def _extract_viz_links(desc):
    """Extract Interactive Visualization links from description HTML.

    Handles two formats:
      New: Interactive Visualization: <a href="URL">Title</a>
      Legacy: Interactive Visualization: Title\\nURL
    """
    viz = []
    # New format: anchor tag with href
    for m in re.finditer(
            r'Interactive Visualization:\s*<a\s+href="([^"]+)"[^>]*>'
            r'([^<]+)</a>', desc):
        viz.append((m.group(2).strip(), m.group(1)))
    # Legacy format: title on one line, bare URL on next
    for m in re.finditer(
            r'Interactive Visualization:\s*([^<\n]+)\n'
            r'(https?://[^\s<]+)', desc):
        url = m.group(2).strip()
        # Skip if already captured via anchor format
        if not any(u == url for _, u in viz):
            viz.append((m.group(1).strip(), url))
    return viz


def _render_card(ep, root_prefix="", episode_url=""):
    """Render a single episode card HTML. root_prefix adjusts relative paths."""
    img_html = ""
    # Prefer thumbnail for card display; fall back to full image
    card_img = ep.get("thumb_url") or ep.get("image_url", "")
    if card_img:
        img_html = (
            f'<img class="card-img" src="{html.escape(card_img)}" '
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
    if episode_url:
        links.append(
            f'<a href="{html.escape(episode_url)}" title="Episode page">'
            f'&#128279; Permalink</a>')
    links_html = " ".join(links)

    audio_html = ""
    if ep["audio_url"]:
        audio_html = (
            f'<audio controls preload="none">'
            f'<source src="{html.escape(ep["audio_url"])}" '
            f'type="audio/mpeg"></audio>')

    # Viz links shown below the date on the card tile
    viz_links = ep.get("viz_links", [])
    viz_html = ""
    if viz_links:
        parts = []
        for title, url in viz_links:
            parts.append(
                f'<a class="card-viz" href="{html.escape(url)}" '
                f'target="_blank">{html.escape(title)}</a>')
        viz_html = "\n    ".join(parts)

    ep_link = f' data-href="{html.escape(episode_url)}"' if episode_url else ''
    title_html = html.escape(ep["title"])
    if episode_url:
        title_html = (
            f'<a class="card-title-link" href="{html.escape(episode_url)}" '
            f'title="Episode page">{title_html}</a>'
        )
    return f"""<div class="card"{ep_link}>
  <div class="card-visual">
    {img_html}
  </div>
  <div class="card-meta">
    <div class="card-title">{title_html}</div>
    <div class="card-date">{html.escape(ep["date"])}</div>
    {viz_html}
  </div>
  <div class="card-body">
    <p class="card-desc">{ep["description"]}</p>
    {audio_html}
    <div class="card-links">{links_html}</div>
  </div>
</div>"""


def _generate_thumbnail(image_url, stem, output_dir):
    """Generate a 128x128 WebP thumbnail from an episode image.

    Args:
        image_url: URL of the original image (on R2).
        stem: File stem to use for thumbnail filename.
        output_dir: Directory to write thumbnail to.

    Returns:
        Path to the generated thumbnail, or None on failure.
    """
    import subprocess
    import tempfile
    import urllib.request

    if not image_url:
        return None

    # Prefer local public image if available; fall back to downloading.
    basename = os.path.basename(urllib.parse.urlparse(image_url).path)
    local_candidate = None
    m = re.match(r"(\d{4})-(\d{2})-", basename)
    if m:
        year, month = m.groups()
        local_candidate = Path(output_dir).parent / "public" / year / month / basename

    try:
        if local_candidate and local_candidate.exists():
            tmp_path = str(local_candidate)
        else:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            req = urllib.request.Request(
                image_url,
                headers={"User-Agent": "podcast-thumbgen/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(tmp_path, "wb") as f:
                    f.write(resp.read())
    except Exception as e:
        print(f"[Thumb] Failed to fetch {image_url}: {e}", file=sys.stderr)
        return None

    # Generate thumbnail using ImageMagick
    thumb_path = Path(output_dir) / "thumbs" / f"{stem}.webp"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)

    cleanup_tmp = not (local_candidate and str(local_candidate) == tmp_path)

    try:
        subprocess.run([
            "magick", tmp_path, "-resize", "128x128",
            "-quality", "80", str(thumb_path)
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"[Thumb] ImageMagick failed for {stem}: {e}", file=sys.stderr)
        if cleanup_tmp:
            os.unlink(tmp_path)
        return None
    except FileNotFoundError:
        print("[Thumb] ImageMagick not found, skipping thumbnail generation",
              file=sys.stderr)
        if cleanup_tmp:
            os.unlink(tmp_path)
        return None

    if cleanup_tmp:
        os.unlink(tmp_path)
    return thumb_path


def _upload_thumbnail(thumb_path):
    """Upload a thumbnail to R2 under thumbs/ prefix.

    Returns the public URL, or None on failure.
    """
    try:
        from r2_upload import get_r2_client, upload_file
        r2 = get_r2_client()
        r2_key = f"thumbs/{thumb_path.name}"
        url = upload_file(r2, str(thumb_path), r2_key,
                          content_type="image/webp")
        return url
    except Exception as e:
        print(f"[Thumb] Upload failed for {thumb_path.name}: {e}",
              file=sys.stderr)
        return None


def generate_index(config, feed_path=None):
    """Generate slim index.html (Netflix-style grid) and per-month archives."""
    import calendar
    from collections import defaultdict

    spotify = config.get("spotify", {})
    show = spotify.get("show", {})
    show_title = show.get("title", "AI Post Transformers")
    github_repo = config.get("github", {}).get("repo", "")

    if feed_path is None:
        feed_path = Path(__file__).parent / spotify.get("feed_file", "podcasts/feed.xml")
    feed_path = Path(feed_path)

    if not feed_path.exists():
        print("[HTML] Skipping index.html: feed.xml not found", file=sys.stderr)
        return None

    episodes = _extract_episodes_from_feed(feed_path)
    total = len(episodes)

    # Generate thumbnails for all episodes that have images but no
    # local thumbnail yet.  This ensures conference and month pages
    # use lightweight WebP thumbs instead of full-size PNGs.
    thumb_dir = feed_path.parent / "thumbs"
    generated = 0
    for ep in episodes:
        img_url = ep.get("image_url", "")
        if not img_url:
            continue
        stem = os.path.splitext(
            os.path.basename(urllib.parse.urlparse(img_url).path))[0]
        if not stem:
            continue
        thumb_path = thumb_dir / f"{stem}.webp"
        if not thumb_path.exists():
            try:
                _generate_thumbnail(img_url, stem, feed_path.parent)
                generated += 1
            except Exception:
                pass
    if generated:
        print(f"{_c('34', '[Thumb]')} Generated {generated} new thumbnails",
              file=sys.stderr)

    # Assign slugs to all episodes (ensure uniqueness)
    seen_slugs = {}
    for ep in episodes:
        slug = _slug_from_title(ep["title"])
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 1
        ep["slug"] = slug

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
    feed_months = set()
    for (year, month), month_eps in sorted_months:
        feed_months.add((year, month))
        label = f"{calendar.month_abbr[month]} {year}"
        href = f"{year}/{month:02d}/index.html"
        archive_links.append(f'<a href="{href}">{html.escape(label)}</a>')

    # Add legacy archive months that aren't already covered by feed episodes
    legacy_months_path = Path("/tmp/r2_archive_months.json")
    if legacy_months_path.exists():
        try:
            legacy_months = json.loads(legacy_months_path.read_text())
            for yr, mo in sorted(legacy_months, reverse=True):
                if (yr, mo) not in feed_months:
                    label = f"{calendar.month_abbr[mo]} {yr}"
                    href = f"{yr}/{mo:02d}/index.html"
                    archive_links.append(f'<a href="{href}">{html.escape(label)}</a>')
        except Exception:
            pass

    archive_html = "\n    ".join(archive_links)

    # Search should cover the entire merged catalog, not just the visible cards.
    # Include any extra legacy slugs as a fallback in case they are not present
    # in the merged feed snapshot.
    search_idx = _build_search_index(episodes, _load_legacy_slugs())
    count = len(search_idx)
    search_json = json.dumps(search_idx).replace("</", "<\\/")

    # Latest 8 episodes for homepage (ordered by publish_date DESC)
    # Episodes are already sorted from feed extraction
    latest = episodes[:8]

    # Generate slim card HTML for each episode
    thumb_base = "https://podcast.do-not-panic.com/thumbs"
    cards_html_parts = []
    for ep in latest:
        slug = ep.get("slug", "")
        title = ep.get("title", "")
        date = ep.get("date", "")
        desc = _normalize_description_html(ep.get("description", ""))
        # Strip HTML tags from description for data attribute
        desc_plain = re.sub(r"<[^>]+>", "", desc)
        desc_plain = html.unescape(desc_plain)[:200]

        # Build thumbnail URL from image URL
        # Extract stem from audio URL or image URL
        img_url = ep.get("image_url", "")
        thumb_url = ""
        if img_url:
            # Image filename is like: 2026-03-16-title-hash.png
            img_name = os.path.basename(img_url)
            stem = os.path.splitext(img_name)[0]
            thumb_url = f"{thumb_base}/{stem}.webp"

            # Best effort: generate local thumbnail so publish-site can upload it.
            thumb_path = feed_path.parent / "thumbs" / f"{stem}.webp"
            if not thumb_path.exists():
                try:
                    _generate_thumbnail(img_url, stem, feed_path.parent)
                except Exception as e:
                    print(f"[Thumb] Generation failed for {stem}: {e}",
                          file=sys.stderr)

        # Search keywords
        search_terms = title.lower()

        card = f'''
  <a class="card" href="episodes/{slug}/" data-t="{html.escape(search_terms)}" data-desc="{html.escape(desc_plain)}">
    <img class="card-img" src="{html.escape(thumb_url)}" alt="" loading="lazy">
    <div class="card-meta"><div class="card-title">{html.escape(title)}</div><div class="card-date">{html.escape(date)}</div></div>
  </a>'''
        cards_html_parts.append(card)

    cards_html = "\n".join(cards_html_parts)

    # GitHub link for nav
    github_html = ""
    if github_repo:
        github_html = f'<a href="https://github.com/{html.escape(github_repo)}" target="_blank">GitHub</a>'

    conferences = [
        ("neurips2025", "NeurIPS 2025"),
        ("icml2024", "ICML 2024"),
        ("iclr2026", "ICLR 2026"),
        ("fast26", "FAST '26"),
    ]
    conferences_html = "\n".join(
        f'<a href="/conference/{html.escape(conf_id)}/">{html.escape(conf_name)}</a>'
        for conf_id, conf_name in conferences
    )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(show_title)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #141414; color: #e0e0e0; line-height: 1.6; }}
.hero {{ position: relative; padding: 3rem 2rem 2rem; text-align: center; background: url("images/podcast-bg-sm.webp") center bottom / cover no-repeat, linear-gradient(180deg, #1a1a2e 0%, #141414 100%); overflow: visible; }}
.hero::before {{ content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(20,20,20,0.45) 0%, #141414 100%), radial-gradient(ellipse at 50% 0%, rgba(229,9,20,0.12) 0%, transparent 70%); pointer-events: none; }}
.hero * {{ position: relative; }}
.hero h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 0.2rem; }}
.hero .tagline {{ color: #999; font-size: 0.85rem; max-width: 460px; margin: 0 auto 0.8rem; }}
.hero-nav {{ display: flex; gap: 0.6rem; justify-content: center; flex-wrap: wrap; }}
.hero-nav a, .hero-nav .dd-btn {{ color: #aaa; text-decoration: none; font-size: 0.78rem; padding: 0.25rem 0.6rem; border: 1px solid #333; border-radius: 20px; cursor: pointer; transition: all 0.15s; display: inline-block; background: none; font-family: inherit; }}
.hero-nav a:hover, .hero-nav .dd-btn:hover {{ color: #fff; border-color: #e50914; }}
.dd-wrap {{ position: relative; display: inline-block; }}
.dd-menu {{ display: none; position: absolute; top: calc(100% + 8px); left: 0; background: #1c1c2e; border: 1px solid #555; border-radius: 10px; padding: 0.5rem 0; min-width: 160px; box-shadow: 0 12px 32px rgba(0,0,0,0.9); z-index: 9999; overflow: visible !important; }}
.dd-menu.open {{ display: block !important; }}
.dd-menu a {{ display: block; padding: 0.6rem 1.2rem; color: #ddd; text-decoration: none; font-size: 0.9rem; border: none; border-radius: 0; }}
.dd-menu a:hover, .dd-menu a:active {{ background: #333; color: #fff; }}
.search-wrap {{ max-width: 800px; margin: 0 auto; padding: 1.2rem 1.5rem 0; }}
.search-wrap input {{ width: 100%; background: #1a1a2a; border: 1px solid #2a2a3a; border-radius: 8px; padding: 0.5rem 0.9rem; color: #e0e0e0; font-size: 0.85rem; outline: none; }}
.search-wrap input:focus {{ border-color: #e50914; }}
.search-wrap input::placeholder {{ color: #555; }}
.search-help {{ max-width: 800px; margin: 0.35rem auto 0; padding: 0 1.5rem; color: #666; font-size: 0.72rem; }}
.section-title {{ max-width: 800px; margin: 1.2rem auto 0.6rem; padding: 0 1.5rem; font-size: 1rem; font-weight: 700; color: #e0e0e0; }}
.section-subtitle {{ max-width: 800px; margin: -0.2rem auto 0.7rem; padding: 0 1.5rem; color: #777; font-size: 0.76rem; }}
.grid {{ max-width: 800px; margin: 0 auto; padding: 0 1rem; display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem; }}
.card {{ background: #1a1a2a; border-radius: 8px; overflow: hidden; cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; text-decoration: none; color: inherit; display: block; }}
.card:hover {{ transform: scale(1.05); box-shadow: 0 8px 30px rgba(229,9,20,0.2); z-index: 10; }}
.card-img {{ width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #222; }}
.card-img-placeholder {{ width: 100%; aspect-ratio: 1; background: linear-gradient(180deg, #232334 0%, #1a1a2a 100%); display: block; }}
.card-meta {{ padding: 0.5rem 0.6rem 0.6rem; }}
.card-title {{ font-size: 0.78rem; font-weight: 600; color: #e0e0e0; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
.card:hover .card-title {{ color: #fff; }}
.card-date {{ font-size: 0.65rem; color: #666; margin-top: 0.25rem; }}
.search-result .card-meta {{ padding-bottom: 0.3rem; }}
.search-result .card-blurb {{ padding: 0 0.6rem 0.75rem; color: #aaa; font-size: 0.73rem; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }}
.search-badge {{ display: inline-block; margin-top: 0.35rem; font-size: 0.62rem; color: #ffb3b7; border: 1px solid #5a2a2d; border-radius: 999px; padding: 0.12rem 0.4rem; }}
.search-empty {{ grid-column: 1 / -1; padding: 1rem; color: #888; text-align: center; border: 1px dashed #2f2f3d; border-radius: 10px; background: #171723; }}
.search-results-wrap {{ display: none; }}
.card-overlay {{ display: none; position: fixed; z-index: 100; background: #1c1c2e; border: 1px solid #333; border-radius: 10px; box-shadow: 0 12px 40px rgba(0,0,0,0.7); max-width: 340px; padding: 1rem; pointer-events: none; }}
.card-overlay.active {{ display: block; }}
.card-overlay .ol-title {{ font-size: 0.9rem; font-weight: 700; margin-bottom: 0.3rem; }}
.card-overlay .ol-date {{ font-size: 0.72rem; color: #888; margin-bottom: 0.5rem; }}
.card-overlay .ol-desc {{ font-size: 0.78rem; color: #bbb; line-height: 1.4; max-height: 120px; overflow: hidden; }}
.archive {{ max-width: 800px; margin: 1.5rem auto 0; padding: 0.5rem 1.5rem 0; }}
.archive h2 {{ font-size: 0.8rem; font-weight: 600; color: #555; margin-bottom: 0.5rem; }}
.archive-months {{ display: flex; flex-wrap: wrap; gap: 0.35rem; }}
.archive-months a {{ font-size: 0.75rem; color: #888; text-decoration: none; padding: 0.2rem 0.5rem; border: 1px solid #252525; border-radius: 4px; transition: all 0.15s; }}
.archive-months a:hover {{ border-color: #e50914; color: #fff; }}
footer {{ max-width: 800px; margin: 1.5rem auto; padding: 1rem 1.5rem; border-top: 1px solid #1e1e1e; color: #444; font-size: 0.72rem; text-align: center; }}
footer a {{ color: #666; text-decoration: none; }}
footer a:hover {{ color: #ccc; }}
.card.hidden {{ display: none; }}
@media (max-width: 480px) {{ .grid {{ grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 0.6rem; }} }}
</style>
</head>
<body>
<div class="hero">
  <h1>{html.escape(show_title)}</h1>
  <p class="tagline">Honest analysis of AI research papers</p>
  <div class="hero-nav">
    <span class="dd-wrap">
      <button class="dd-btn" id="btn-listen" onclick="event.stopPropagation(); document.getElementById('dd-listen').classList.toggle('open')">Listen &#9662;</button>
      <div class="dd-menu" id="dd-listen">
        <a href="https://open.spotify.com/show/48ygM4upvm6noxCbmhlz8i" target="_blank">&#127911; Spotify</a>
        <a href="https://podcasts.apple.com/us/podcast/ai-post-transformers/id1835878324" target="_blank">&#127822; Apple Podcasts</a>
        <a href="https://music.amazon.com/podcasts/bad34c42-bf59-4b9a-8085-5742fd63e011/ai-post-transformers" target="_blank">&#128309; Amazon Music</a>
        <a href="/feed.xml" target="_blank">&#128225; RSS Feed</a>
      </div>
    </span>
    <a href="/queue.html">Queue</a>
    <a href="/conference/announcements/">Announcements</a>
    <span class="dd-wrap">
      <button class="dd-btn" id="btn-conferences" onclick="event.stopPropagation(); document.getElementById('dd-conferences').classList.toggle('open')">Conferences &#9662;</button>
      <div class="dd-menu" id="dd-conferences">
        {conferences_html}
      </div>
    </span>
    <a href="/sponsor.html">Sponsor</a>
    <a href="/sister-podcasts.html">More Shows</a>
    {github_html}
  </div>
</div>
<div class="search-wrap"><input type="text" id="search" placeholder="Search all episodes, including legacy podcasts..." autocomplete="off"></div>
<div class="search-help">Searches the full catalog, including legacy episodes from the early archive.</div>
<div class="section-title" id="latest-title">Latest Episodes</div>
<div class="grid" id="episodes">
{cards_html}
</div>
<div class="search-results-wrap" id="search-results-wrap">
  <div class="section-title" id="search-results-title">Search Results</div>
  <div class="section-subtitle" id="search-results-summary"></div>
  <div class="grid" id="search-results"></div>
</div>
<div id="overlay" class="card-overlay"></div>
<div class="archive">
  <h2>Archive</h2>
  <div class="archive-months">
    {archive_html}
  </div>
</div>
<footer>{html.escape(show_title)} &middot; <a href="https://github.com/{html.escape(github_repo) if github_repo else 'mcgrof/ai-post-transformers'}">source</a> &middot; <a href="/feed.xml">rss</a></footer>
<script>
const searchIndex = {search_json};
const searchInput = document.getElementById('search');
const latestTitle = document.getElementById('latest-title');
const latestGrid = document.getElementById('episodes');
const searchResultsWrap = document.getElementById('search-results-wrap');
const searchResultsTitle = document.getElementById('search-results-title');
const searchResultsSummary = document.getElementById('search-results-summary');
const searchResults = document.getElementById('search-results');

function escapeHtml(value) {{
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

function normalizeSearchText(value) {{
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9.\s-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}}

function countOccurrences(haystack, needle) {{
  if (!needle) return 0;
  let count = 0;
  let idx = haystack.indexOf(needle);
  while (idx !== -1) {{
    count += 1;
    idx = haystack.indexOf(needle, idx + needle.length);
  }}
  return count;
}}

function scoreEpisode(item, terms) {{
  const title = normalizeSearchText(item.t || '');
  const queryText = normalizeSearchText(item.q || item.x || '');
  const date = normalizeSearchText(item.d || '');
  let score = 0;
  for (const term of terms) {{
    const inTitle = title.includes(term);
    const inDesc = queryText.includes(term);
    const inDate = date.includes(term);
    if (!inTitle && !inDesc && !inDate) return -1;
    if (title === term) score += 100;
    else if (title.startsWith(term)) score += 45;
    else if (inTitle) score += 28;
    if (inDesc) {{
      score += 8;
      const first = queryText.indexOf(term);
      if (first >= 0) score += Math.max(0, 18 - Math.min(18, Math.floor(first / 120)));
      score += Math.min(8, countOccurrences(queryText, term));
    }}
    if (inDate) score += 2;
  }}
  if (item.l) score -= 1;
  return score;
}}

function renderSearchResults(items, query) {{
  if (!items.length) {{
    searchResults.innerHTML = '<div class="search-empty">No episodes matched “' + escapeHtml(query) + '”.</div>';
    return;
  }}
  searchResults.innerHTML = items.map(item => {{
    const image = item.i
      ? '<img class="card-img" src="' + escapeHtml(item.i) + '" alt="" loading="lazy">'
      : '<div class="card-img card-img-placeholder"></div>';
    const badge = item.l ? '<div class="search-badge">Legacy</div>' : '';
    const blurb = item.x ? '<div class="card-blurb">' + escapeHtml(item.x) + '</div>' : '';
    return '<a class="card search-result" href="' + escapeHtml(item.u) + '">' +
      image +
      '<div class="card-meta"><div class="card-title">' + escapeHtml(item.t) + '</div><div class="card-date">' + escapeHtml(item.d) + '</div>' + badge + '</div>' +
      blurb +
      '</a>';
  }}).join('');
}}

searchInput.addEventListener('input', function() {{
  const rawQuery = this.value;
  const q = normalizeSearchText(rawQuery);
  if (!q) {{
    latestTitle.style.display = '';
    latestGrid.style.display = 'grid';
    searchResultsWrap.style.display = 'none';
    searchResults.innerHTML = '';
    searchResultsSummary.textContent = '';
    return;
  }}

  const terms = q.split(/\s+/).filter(Boolean);
  const matches = searchIndex
    .map(item => ({{ item, score: scoreEpisode(item, terms) }}))
    .filter(entry => entry.score >= 0)
    .sort((a, b) => b.score - a.score || (a.item.t || '').localeCompare(b.item.t || ''))
    .slice(0, 60)
    .map(entry => entry.item);

  latestTitle.style.display = 'none';
  latestGrid.style.display = 'none';
  searchResultsWrap.style.display = 'block';
  searchResultsTitle.textContent = 'Search Results';
  searchResultsSummary.textContent = matches.length + ' match' + (matches.length === 1 ? '' : 'es') + ' for “' + q + '”';
  renderSearchResults(matches, q);
}});

const overlay = document.getElementById('overlay');
let hoverTimeout;
document.querySelectorAll('#episodes .card').forEach(card => {{
  card.addEventListener('mouseenter', (e) => {{ clearTimeout(hoverTimeout); hoverTimeout = setTimeout(() => {{ const desc = card.dataset.desc || ''; if (!desc) return; overlay.innerHTML = '<div class="ol-title">' + card.querySelector('.card-title').textContent + '</div><div class="ol-desc">' + desc + '</div>'; const rect = card.getBoundingClientRect(); overlay.style.left = Math.min(rect.right + 10, window.innerWidth - 360) + 'px'; overlay.style.top = Math.max(rect.top, 10) + 'px'; overlay.classList.add('active'); }}, 400); }});
  card.addEventListener('mouseleave', () => {{ clearTimeout(hoverTimeout); overlay.classList.remove('active'); }});
}});
document.addEventListener('click', function(e) {{ if (!e.target.closest('.dd-wrap')) {{ document.querySelectorAll('.dd-menu.open').forEach(function(m) {{ m.classList.remove('open'); }}); }} }});
</script>
</body>
</html>
"""

    index_path = feed_path.parent / "index.html"
    index_path.write_text(page)
    print(f"{_c('34', '[HTML]')} Index written to {_c('2', str(index_path))} "
          f"({_c('1', str(count))} episodes)",
          file=sys.stderr)

    # Generate individual episode pages
    _generate_episode_pages(episodes, show_title, feed_path.parent)

    # Generate per-month archive pages
    _generate_month_pages(sorted_months, show_title, feed_path.parent)

    # Generate about + sponsor pages alongside index
    generate_about(config, feed_path.parent)
    generate_sponsor_page(config, feed_path.parent)

    # Generate conference pages alongside index
    _generate_conference_pages(episodes, show_title, feed_path.parent)

    return str(index_path)


def _generate_conference_pages(episodes, show_title, output_dir):
    """Generate public conference pages with the same look/feel as monthly archives."""
    conferences = {
        "announcements": {
            "name": "Announcements",
            "episode_urls": [
                "/episodes/cognizant-new-work-new-world-2026/",
                "/episodes/were-open-source-new-home-visualizations-and-how-to-shape-our-queue/",
                "/episodes/new-voices-same-nerds-the-kokoro-tts-episode/",
            ],
        },
        "neurips2025": {
            "name": "NeurIPS 2025",
            "episode_urls": [
                "/episodes/adaptive-compression-techniques-for-efficient-llm-inference/",
                "/episodes/flashattention-4-conquers-asymmetric-gpu-hardware-scaling/",
                "/episodes/long-context-dichotomy-of-findings-status-of-research/",
                "/episodes/movement-pruning-adaptive-sparsity-by-fine-tuning/",
                "/episodes/neurips-2025-a-mem-agentic-memory-for-llm-agents/",
                "/episodes/neurips-2025-agentic-plan-caching-test-time-memory-for-fast-and-cost-efficient-l/",
                "/episodes/neurips-2025-dynaact-large-language-model-reasoning-with-dynamic-action-spaces/",
                "/episodes/neurips-2025-flashbias-fast-computation-of-attention-with-bias/",
                "/episodes/neurips-2025-gated-attention-for-large-language-models-non-linearity-sparsity-an/",
                "/episodes/neurips-2025-homogeneous-keys-heterogeneous-values/",
                "/episodes/neurips-2025-kggen-extracting-knowledge-graphs-from-plain-text-with-language-mod/",
                "/episodes/neurips-2025-l2m-mutual-information-scaling-law-for-long-context-language-modeli/",
                "/episodes/neurips-2025-large-language-diffusion-models/",
                "/episodes/neurips-2025-moba-mixture-of-block-attention-for-long-context-llms/",
                "/episodes/neurips-2025-parallel-scaling-law-for-language-models/",
                "/episodes/neurips-2025-reinforcement-learning-for-reasoning-in-large-language-models-with/",
                "/episodes/neurips-2025-reward-reasoning-model/",
                "/episodes/neurips-2025-self-adapting-language-models/",
                "/episodes/neurips-2025-serl-self-play-reinforcement-learning-for-large-language-models-wit/",
                "/episodes/neurips-2025-thinkless-llm-learns-when-to-think/",
                "/episodes/random-walk-methods-for-graph-learning-and-networks/",
                "/episodes/squisher-approximating-the-fisher-information-matrix-and-use-cases/",
                "/episodes/tokenization-bias-the-hidden-flaw-breaking-language-models/",
                "/episodes/why-cartridge-works-keys-as-routers-in-kv-caches/",
            ],
        },
        "icml2024": {
            "name": "ICML 2024",
            "episode_urls": [
                "/episodes/structured-state-space-duality-unifies-transformers-and-ssms/",
            ],
        },
        "iclr2026": {
            "name": "ICLR 2026",
            "episode_urls": [
                "/episodes/advancing-mechanistic-interpretability-with-sparse-autoencoders/",
                "/episodes/gradient-descent-at-inference-time-for-llm-reasoning/",
                "/episodes/tokenization-bias-the-hidden-flaw-breaking-language-models/",
            ],
        },
        "fast26": {
            "name": "FAST '26",
            "episode_urls": [
                "/episodes/cacheslide-position-aware-kv-cache-reuse-for-agent-llms/",
                "/episodes/bidaw-computation-storage-aware-kv-caching-for-llms/",
                "/episodes/accelerating-llm-cold-starts-with-programmable-page-cache/",
                "/episodes/solidattention-co-designing-sparse-attention-and-ssd-io/",
                "/episodes/generative-file-systems-replacing-code-with-formal-specifications/",
                "/episodes/xerxes-cxl-30-simulation-for-scalable-memory-systems/",
                "/episodes/optimizing-mixture-of-block-attention-through-statistical-theory/",
                "/episodes/dualpath-breaks-storage-bandwidth-bottleneck-in-agentic-inference/",
            ],
        },
    }

    episode_by_url = {f"/episodes/{ep['slug']}/": ep for ep in episodes if ep.get('slug')}

    archive_style = """*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto,
               Helvetica, Arial, sans-serif;
  background: #141414;
  color: #e5e5e5;
  line-height: 1.5;
  min-height: 100vh;
}
a { color: #e50914; text-decoration: none; }
a:hover { text-decoration: underline; }
.page-header {
  padding: 2rem 1.5rem 1rem;
  max-width: 1200px;
  margin: 0 auto;
}
.back { font-size: 0.85rem; color: #777; }
.back:hover { color: #e5e5e5; }
h1 {
  font-size: 1.4rem;
  font-weight: 600;
  color: #fff;
  margin-top: 0.8rem;
}
.section {
  max-width: 1200px;
  margin: 0 auto;
  padding: 1rem 1.5rem 2rem;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 1.2rem;
}
.card {
  position: relative;
  border-radius: 6px;
  overflow: visible;
  cursor: pointer;
}
.card-visual {
  position: relative;
  aspect-ratio: 1 / 1;
  border-radius: 6px;
  overflow: hidden;
  background: #222;
  transition: transform 0.3s cubic-bezier(.25,.46,.45,.94),
              box-shadow 0.3s ease;
  z-index: 1;
}
.card:hover .card-visual {
  transform: scale(1.05);
  box-shadow: 0 14px 36px rgba(0,0,0,0.7);
  z-index: 10;
  border-radius: 6px 6px 0 0;
}
.card-img {
  width: 100%; height: 100%;
  object-fit: cover;
  transition: transform 0.4s ease;
}
.card:hover .card-img { transform: scale(1.1); }
.card-img-placeholder {
  width: 100%; height: 100%;
  background: linear-gradient(135deg, #1a1a2e, #2d2d44);
}
.card-meta {
  position: relative;
  padding: 0.6rem 0.5rem 0.3rem;
  background: #1a1a1a;
  transition: transform 0.3s cubic-bezier(.25,.46,.45,.94);
  z-index: 2;
}
.card:hover .card-meta {
  transform: scale(1.05);
  z-index: 11;
}
.card-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: #e5e5e5;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-title-link {
  color: inherit;
  text-decoration: none;
}
.card-title-link:hover {
  color: #fff;
  text-decoration: underline;
}
.card-date {
  font-size: 0.72rem;
  color: #777;
  margin-top: 0.15rem;
}
.card-viz {
  display: block;
  font-size: 0.7rem;
  color: #5eeacd;
  margin-top: 0.25rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-viz:hover {
  color: #fff;
  text-decoration: underline;
}
.card-viz-desc {
  font-size: 0.8rem;
  margin-top: 0.6rem;
  color: #5eeacd;
}
.card-viz-desc a {
  color: #5eeacd;
}
.card-viz-desc a:hover {
  color: #fff;
}
.card-body {
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
}
.card:hover .card-body,
.card.active .card-body {
  opacity: 1;
  visibility: visible;
  transform: translate(-50%, 0);
  pointer-events: auto;
}
.card.active .card-visual {
  transform: scale(1.05);
  box-shadow: 0 14px 36px rgba(0,0,0,0.7);
  z-index: 10;
  border-radius: 6px 6px 0 0;
}
.card.active .card-meta {
  transform: scale(1.05);
  z-index: 11;
}
.card-desc {
  font-size: 0.82rem;
  color: #e5e5e5;
  line-height: 1.6;
  margin-bottom: 0.6rem;
}
.card-sources {
  margin-top: 0.8rem;
  padding-top: 0.6rem;
  border-top: 1px solid #333;
  font-size: 0.75rem;
  color: #999;
  line-height: 1.7;
}
.card-sources a {
  color: #7ab;
  word-break: break-all;
}
.card-sources a:hover { color: #e50914; }
.card-body audio {
  width: 100%;
  height: 34px;
  margin-bottom: 0.4rem;
  border-radius: 4px;
}
.card-links {
  display: flex;
  gap: 1rem;
  font-size: 0.75rem;
}
.card-links a { color: #999; }
.card-links a:hover { color: #e50914; text-decoration: none; }
.card.expand-up .card-body {
  top: auto;
  bottom: 100%;
  border-radius: 6px 6px 0 0;
  transform-origin: bottom center;
  transform: translate(-50%, 8px);
}
.card.expand-up:hover .card-body,
.card.expand-up.active .card-body {
  transform: translate(-50%, 0);
}
.card.expand-up:hover .card-visual,
.card.expand-up.active .card-visual {
  border-radius: 0 0 6px 6px;
}
.footer {
  text-align: center;
  padding: 2rem;
  color: #555;
  font-size: 0.78rem;
  border-top: 1px solid #222;
  margin-top: 0;
}
@media (max-width: 600px) {
  .grid {
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 0.8rem;
  }
  .card-body {
    position: fixed;
    top: auto; bottom: 0; left: 0;
    width: 100%; max-width: 100vw; max-height: 70vh;
    overflow-y: auto;
    border-radius: 12px 12px 0 0;
    transform: translateY(100%);
    opacity: 0; visibility: hidden;
    z-index: 1000; pointer-events: none;
    transition: transform 0.3s ease, opacity 0.25s ease, visibility 0.25s ease;
  }
  .card.active .card-body {
    transform: translateY(0);
    opacity: 1; visibility: visible; pointer-events: auto;
  }
  .card-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.5); z-index: 999;
  }
  .card.active + .card-overlay, .card-overlay.active { display: block; }
  .card-visual { border-radius: 6px; }
  .card:hover .card-visual { transform: none; box-shadow: none; border-radius: 6px; }
  .card:hover .card-meta { transform: none; }
  .card:hover .card-body { transform: translateY(100%); opacity: 0; visibility: hidden; }
  .card.active .card-body,
  .card.active:hover .card-body { transform: translateY(0); opacity: 1; visibility: visible; pointer-events: auto; }
}
"""

    card_js = """<script>
document.querySelectorAll('.card').forEach(card => {
  card.addEventListener('click', (e) => {
    if (e.target.closest('audio, a')) return;
    const wasActive = card.classList.contains('active');
    document.querySelectorAll('.card.active').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.card-overlay.active').forEach(o => o.classList.remove('active'));
    if (!wasActive) {
      card.classList.add('active');
      const overlay = card.nextElementSibling;
      if (overlay && overlay.classList.contains('card-overlay')) overlay.classList.add('active');
    }
  });
});
document.querySelectorAll('.card-overlay').forEach(overlay => {
  overlay.addEventListener('click', () => {
    overlay.classList.remove('active');
    const prev = overlay.previousElementSibling;
    if (prev && prev.classList.contains('card')) prev.classList.remove('active');
  });
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.card')) {
    document.querySelectorAll('.card.active').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.card-overlay.active').forEach(o => o.classList.remove('active'));
  }
});
</script>"""

    for conf_id, conf in conferences.items():
        conf_dir = Path(output_dir) / "conference" / conf_id
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_eps = [episode_by_url[url] for url in conf["episode_urls"] if url in episode_by_url]
        conf_eps.sort(key=lambda e: e.get("date_dt") or datetime.min, reverse=True)
        cards = []
        for ep in conf_eps:
            episode_url = f"../../episodes/{ep['slug']}/"
            cards.append(_render_card(ep, root_prefix="../../", episode_url=episode_url))
            cards.append('<div class="card-overlay"></div>')
        cards_html = "\n".join(cards) if cards else '<p>No public episodes yet.</p>'
        page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(conf['name'])} &mdash; {html.escape(show_title)}</title>
<style>
{archive_style}
</style>
</head>
<body>

<div class="page-header">
  <a class="back" href="../../index.html">&larr; All episodes</a>
  <h1>{html.escape(conf['name'])}</h1>
</div>

<div class="section">
  <div class="grid">
{cards_html}
  </div>
</div>

<div class="footer">
  {html.escape(show_title)} &middot; <a href="../../index.html">Home</a>
</div>
{card_js}
</body>
</html>
"""
        (conf_dir / "index.html").write_text(page)

    print(f"{_c('34', '[HTML]')} Generated {_c('1', str(len(conferences)))} conference page(s)", file=sys.stderr)


def _generate_episode_pages(episodes, show_title, output_dir):
    """Generate individual episode pages under episodes/{slug}/index.html."""
    for ep in episodes:
        slug = ep.get("slug", "")
        if not slug:
            continue

        ep_dir = Path(output_dir) / "episodes" / slug
        ep_dir.mkdir(parents=True, exist_ok=True)

        img_html = ""
        if ep["image_url"]:
            img_html = (
                f'<img class="ep-cover" src="{html.escape(ep["image_url"])}" '
                f'alt="{html.escape(ep["title"])}" loading="lazy">')

        audio_html = ""
        if ep["audio_url"]:
            audio_html = (
                f'<audio controls preload="none" style="width:100%;margin:1.5rem 0;">'
                f'<source src="{html.escape(ep["audio_url"])}" '
                f'type="audio/mpeg"></audio>')

        links = []
        if ep["audio_url"]:
            links.append(
                f'<a href="{html.escape(ep["audio_url"])}" download>'
                f'&#11015; Download MP3</a>')
        if ep["srt_url"]:
            links.append(
                f'<a href="{html.escape(ep["srt_url"])}">'
                f'&#128196; Subtitles</a>')
        links_html = " &nbsp;&middot;&nbsp; ".join(links)

        page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(ep["title"])} &mdash; {html.escape(show_title)}</title>
<meta name="description" content="{html.escape(ep['title'])}">
<meta property="og:title" content="{html.escape(ep['title'])}">
<meta property="og:type" content="article">
{"" if not ep["image_url"] else f'<meta property="og:image" content="{html.escape(ep["image_url"])}">' }
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
.container {{
  max-width: 720px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}}
.back {{
  font-size: 0.85rem;
  color: #777;
  display: inline-block;
  margin-bottom: 1.5rem;
}}
.back:hover {{ color: #e5e5e5; }}
.ep-cover {{
  width: 100%;
  max-width: 400px;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0,0,0,0.5);
  margin-bottom: 1.5rem;
}}
h1 {{
  font-size: 1.5rem;
  font-weight: 700;
  color: #fff;
  margin-bottom: 0.3rem;
  line-height: 1.3;
}}
.ep-date {{
  font-size: 0.85rem;
  color: #777;
  margin-bottom: 1rem;
}}
.ep-links {{
  font-size: 0.85rem;
  margin-bottom: 1.5rem;
  color: #999;
}}
.ep-links a {{ color: #999; }}
.ep-links a:hover {{ color: #e50914; }}
.ep-desc {{
  font-size: 0.92rem;
  color: #e5e5e5;
  line-height: 1.7;
}}
.ep-desc .card-sources {{
  margin-top: 1.2rem;
  padding-top: 0.8rem;
  border-top: 1px solid #333;
  font-size: 0.82rem;
  color: #999;
  line-height: 1.8;
}}
.ep-desc .card-sources a {{
  color: #7ab;
  word-break: break-all;
}}
.ep-desc .card-sources a:hover {{ color: #e50914; }}
.ep-desc .card-viz-desc {{
  font-size: 0.88rem;
  margin-top: 0.8rem;
  color: #5eeacd;
}}
.ep-desc .card-viz-desc a {{
  color: #5eeacd;
}}
.ep-desc .card-viz-desc a:hover {{
  color: #fff;
}}
.footer {{
  text-align: center;
  padding: 2rem;
  color: #555;
  font-size: 0.78rem;
  border-top: 1px solid #222;
  margin-top: 2rem;
}}
</style>
</head>
<body>
<div class="container">
  <a class="back" href="../../index.html">&larr; All episodes</a>
  {img_html}
  <h1>{html.escape(ep["title"])}</h1>
  <div class="ep-date">{html.escape(ep["date"])}</div>
  {audio_html}
  <div class="ep-links">{links_html}</div>
  <div class="ep-desc">{_normalize_description_html(ep["description"])}</div>
</div>

<div class="footer">
  {html.escape(show_title)} &middot; <a href="../../index.html">Home</a>
</div>
</body>
</html>
"""
        ep_path = ep_dir / "index.html"
        ep_path.write_text(page)

    print(f"{_c('34', '[HTML]')} Generated {_c('1', str(len(episodes)))} "
          f"episode page(s)", file=sys.stderr)


def _generate_month_pages(sorted_months, show_title, output_dir):
    """Generate per-month archive pages under YYYY/MM/index.html."""
    import calendar

    month_nav_links = []
    for (nav_year, nav_month), _ in sorted_months:
        nav_label = f"{calendar.month_abbr[nav_month]} {nav_year}"
        nav_href = f"../../{nav_year}/{nav_month:02d}/index.html"
        month_nav_links.append(f'<a href="{nav_href}">{html.escape(nav_label)}</a>')
    month_nav_html = "\n    ".join(month_nav_links)

    for (year, month), month_eps in sorted_months:
        label = f"{calendar.month_name[month]} {year}"
        cards_html = "\n".join(
            _render_card(ep, root_prefix="../../",
                         episode_url=f"../../episodes/{ep['slug']}/")
            for ep in month_eps)

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
  cursor: pointer;
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
.card-title-link {{
  color: inherit;
  text-decoration: none;
}}
.card-title-link:hover {{
  color: #fff;
  text-decoration: underline;
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
.card-viz-desc {{
  font-size: 0.8rem;
  margin-top: 0.6rem;
  color: #5eeacd;
}}
.card-viz-desc a {{
  color: #5eeacd;
}}
.card-viz-desc a:hover {{
  color: #fff;
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
.card:hover .card-body,
.card.active .card-body {{
  opacity: 1;
  visibility: visible;
  transform: translate(-50%, 0);
  pointer-events: auto;
}}
.card.active .card-visual {{
  transform: scale(1.05);
  box-shadow: 0 14px 36px rgba(0,0,0,0.7);
  z-index: 10;
  border-radius: 6px 6px 0 0;
}}
.card.active .card-meta {{
  transform: scale(1.05);
  z-index: 11;
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
.card.expand-up:hover .card-body,
.card.expand-up.active .card-body {{
  transform: translate(-50%, 0);
}}
.card.expand-up:hover .card-visual,
.card.expand-up.active .card-visual {{
  border-radius: 0 0 6px 6px;
}}
.archive {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 1.5rem 1.5rem;
}}
.archive h2 {{
  font-size: 0.8rem;
  font-weight: 600;
  color: #777;
  margin-bottom: 0.5rem;
}}
.archive-months {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}}
.archive-months a {{
  font-size: 0.75rem;
  color: #888;
  text-decoration: none;
  padding: 0.2rem 0.5rem;
  border: 1px solid #252525;
  border-radius: 4px;
  transition: all 0.15s;
}}
.archive-months a:hover {{ border-color: #e50914; color: #fff; }}
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
  .card.active .card-body,
  .card.active:hover .card-body {{ transform: translateY(0); opacity: 1; visibility: visible; pointer-events: auto; }}
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

<div class="archive">
  <h2>Browse by month</h2>
  <div class="archive-months">
    {month_nav_html}
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
  var overlay = document.getElementById('card-overlay');
  function closeActive() {{
    var a = document.querySelector('.card.active');
    if (a) a.classList.remove('active');
    overlay.classList.remove('active');
  }}
  var isTouchDevice = 'ontouchstart' in window;
  cards.forEach(function(c) {{
    c.addEventListener('click', function(e) {{
      if (e.target.closest('a, audio, button')) return;
      e.preventDefault();
      e.stopPropagation();
      var href = c.getAttribute('data-href');
      if (href) {{ window.location.href = href; return; }}
      var w = c.classList.contains('active');
      closeActive();
      if (!w) {{ c.classList.add('active'); overlay.classList.add('active'); }}
    }});
    if (!isTouchDevice) {{
      c.addEventListener('mouseenter', function() {{
        closeActive();
        c.classList.add('active');
        overlay.classList.add('active');
      }});
      c.addEventListener('mouseleave', function() {{
        c.classList.remove('active');
        overlay.classList.remove('active');
      }});
    }}
  }});
  overlay.addEventListener('click', closeActive);
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') closeActive();
  }});
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


def generate_sponsor_page(config, output_dir):
    """Generate sponsor.html with support links and contributor credits."""
    sponsor_cfg = config.get("sponsors", {})
    contributors = sponsor_cfg.get("contributors", [])
    spotify = config.get("spotify", {})
    show = spotify.get("show", {})
    show_title = show.get("title", "AI Post Transformers")

    intro = html.escape(sponsor_cfg.get("intro", "Support the project and the people donating credits and time to make it run."))
    primary = sponsor_cfg.get("links", {}).get("primary", {})
    primary_label = html.escape(primary.get("label", "Sponsor this project"))
    primary_url = html.escape(primary.get("url", "https://github.com/sponsors/mcgrof"))
    featured = sponsor_cfg.get("featured_episode", {})
    featured_html = ""
    if featured:
        feat_title = html.escape(featured.get("title", "Announcement episode"))
        feat_url = html.escape(featured.get("url", "/index.html"))
        feat_desc = html.escape(featured.get("description", ""))
        featured_html = f"""<div class=\"featured-episode\">
  <div class=\"featured-kicker\">Listen first</div>
  <div class=\"featured-title\"><a href=\"{feat_url}\">{feat_title}</a></div>
  <p class=\"featured-desc\">{feat_desc}</p>
</div>"""

    cards = []
    for person in contributors:
        name = html.escape(person.get("name", "Contributor"))
        role = html.escape(person.get("role", "Contributor"))
        profile = html.escape(person.get("profile_url", "#"))
        support_url = html.escape(person.get("support_url", primary_url))
        support_label = html.escape(person.get("support_label", "Sponsor"))
        blurb = html.escape(person.get("blurb", ""))
        services = "".join(
            f'<li>{html.escape(service)}</li>' for service in person.get("services", [])
        )
        cards.append(f"""<div class=\"sponsor-card\">
  <div class=\"sponsor-head\">
    <div>
      <div class=\"sponsor-name\"><a href=\"{profile}\" target=\"_blank\">{name}</a></div>
      <div class=\"sponsor-role\">{role}</div>
    </div>
    <a class=\"sponsor-btn\" href=\"{support_url}\" target=\"_blank\">{support_label}</a>
  </div>
  <p class=\"sponsor-blurb\">{blurb}</p>
  <div class=\"services-title\">Existing credits / services in use</div>
  <ul class=\"services\">{services}</ul>
</div>""")

    cards_html = "\n".join(cards) if cards else "<p>No contributors listed yet.</p>"
    page = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Sponsor &mdash; {html.escape(show_title)}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif; background: #141414; color: #e5e5e5; line-height: 1.6; min-height: 100vh; }}
a {{ color: #e50914; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.page {{ max-width: 820px; margin: 0 auto; padding: 3rem 1.5rem 2rem; }}
.back {{ font-size: 0.85rem; color: #777; margin-bottom: 2rem; display: inline-block; }}
.back:hover {{ color: #e5e5e5; }}
h1 {{ font-size: 1.8rem; font-weight: 700; color: #fff; margin-bottom: 0.5rem; }}
.subtitle {{ color: #999; font-size: 0.95rem; margin-bottom: 1.5rem; }}
.cta {{ display:inline-block; margin-bottom: 2rem; background:#e50914; color:#fff; padding:0.8rem 1rem; border-radius:8px; font-weight:600; }}
.cta:hover {{ text-decoration:none; filter:brightness(1.08); }}
.featured-episode {{ background:#191922; border:1px solid #2e2e44; border-radius:10px; padding:1rem 1.1rem; margin:0 0 1.4rem; }}
.featured-kicker {{ color:#888; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.3rem; }}
.featured-title {{ font-size:1rem; font-weight:700; margin-bottom:0.35rem; }}
.featured-desc {{ color:#aaa; font-size:0.88rem; }}
.sponsor-card {{ background:#1a1a1a; border:1px solid #2b2b2b; border-radius:10px; padding:1.2rem; margin-bottom:1rem; }}
.sponsor-head {{ display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; margin-bottom:0.8rem; }}
.sponsor-name {{ font-size:1.05rem; font-weight:700; color:#fff; }}
.sponsor-role {{ color:#888; font-size:0.85rem; margin-top:0.15rem; }}
.sponsor-btn {{ color:#fff; background:#2a2a2a; border:1px solid #444; padding:0.45rem 0.7rem; border-radius:8px; white-space:nowrap; }}
.sponsor-btn:hover {{ text-decoration:none; border-color:#e50914; }}
.sponsor-blurb {{ color:#bbb; margin-bottom:0.8rem; }}
.services-title {{ color:#888; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:0.4rem; }}
.services {{ padding-left:1.2rem; color:#ddd; }}
.services li {{ margin:0.25rem 0; }}
.footer {{ text-align: center; padding: 2rem; color: #555; font-size: 0.78rem; border-top: 1px solid #222; margin-top: 2rem; }}
@media (max-width: 560px) {{ .sponsor-head {{ flex-direction:column; }} .sponsor-btn {{ align-self:flex-start; }} }}
</style>
</head>
<body>
<div class=\"page\">
  <a class=\"back\" href=\"index.html\">&larr; Back to episodes</a>
  <h1>Sponsor</h1>
  <p class=\"subtitle\">{intro}</p>
  <a class=\"cta\" href=\"{primary_url}\" target=\"_blank\">{primary_label}</a>
  {featured_html}
  {cards_html}
</div>
<div class=\"footer\">{html.escape(show_title)}</div>
</body>
</html>
"""
    out_path = Path(output_dir) / "sponsor.html"
    out_path.write_text(page)
    print(f"{_c('34', '[HTML]')} Sponsor page written to {_c('2', str(out_path))}", file=sys.stderr)
    return str(out_path)


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
