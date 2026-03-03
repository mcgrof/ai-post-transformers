"""RSS 2.0 feed generator for Spotify podcast distribution."""

import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from db import get_connection, init_db, list_podcasts

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


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

    # Enclosure (audio file)
    audio_file = episode.get("audio_file", "")
    if audio_file:
        filename = os.path.basename(audio_file)
        if audio_base_url:
            url = audio_base_url.rstrip("/") + "/" + filename
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
            image_url = audio_base_url.rstrip("/") + "/" + image_filename
        else:
            image_url = image_filename
        ET.SubElement(item, "{%s}image" % ITUNES_NS, href=image_url)

    # Transcript (SRT file — Podcast Index namespace)
    if audio_file:
        srt_path = os.path.splitext(audio_file)[0] + ".srt"
        if os.path.exists(srt_path):
            srt_filename = os.path.basename(srt_path)
            if audio_base_url:
                srt_url = audio_base_url.rstrip("/") + "/" + srt_filename
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

    # Build set of titles from local episodes that should replace Anchor versions
    # (e.g., the Cognizant episode exists both on Anchor and locally)
    local_titles = set()
    for ep in local_episodes:
        title = ep.get("title", "").replace("Episode: ", "")
        local_titles.add(title.strip().lower())

    # Add new local episodes first (newest at top)
    new_count = 0
    for ep in local_episodes:
        _add_episode(channel, ep, audio_base_url)
        new_count += 1

    # Build set of Anchor GUIDs to exclude (replaced by new pipeline)
    replaced_guids = set(
        spotify.get("replaced_anchor_guids", [])
    )

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
        channel.append(item)
        legacy_count += 1

    # Write feed
    feed_path = Path(__file__).parent / feed_file
    feed_path.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(str(feed_path), xml_declaration=True, encoding="UTF-8")

    total = new_count + legacy_count
    print(f"[RSS] Feed written to {feed_path} "
          f"({new_count} new + {legacy_count} legacy = {total} total episodes)",
          file=sys.stderr)
    return str(feed_path)
