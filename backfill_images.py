#!/usr/bin/env python3
"""Backfill cover art for podcast episodes missing custom images.

Scans the Anchor RSS feed and local DB for episodes using a
generic placeholder image or no image at all. Generates custom
cover art via OpenAI gpt-image-1 and uploads to R2. Updates
the anchor_feed.xml and DB records so the RSS feed picks up
the new images on next regeneration.

Usage:
    .venv/bin/python backfill_images.py          # generate + upload
    .venv/bin/python backfill_images.py --dry-run # show what would run
"""

import argparse
import hashlib
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from db import get_connection, init_db, list_podcasts, update_podcast
from image_gen import generate_episode_image
from r2_upload import get_r2_client, upload_file

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
NOLOGO_MARKER = "uploaded_nologo"
OUTPUT_DIR = Path(__file__).parent / "generated_covers"


def _c(code, text):
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return text
    return f"\033[{code}m{text}\033[0m"


def _log(tag, msg):
    print(f"{_c('35', '[Backfill]')} {_c('1', tag)}: {msg}",
          file=sys.stderr)


def _slugify(text, max_len=50):
    """Convert title to a filesystem-safe slug."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")[:max_len].rstrip("-")
    return s


def _stable_filename(title):
    """Generate a stable filename from episode title."""
    slug = _slugify(title)
    h = hashlib.sha256(title.encode()).hexdigest()[:8]
    return f"{slug}-{h}.png"


def _build_prompt(config, title, description):
    """Build image generation prompt from episode metadata."""
    img_config = config.get("image_generation", {})
    style = img_config.get("style_prompt", "").strip()

    prompt = f"""{style}

Create a DARK-THEMED INFOGRAPHIC for a podcast episode cover image.

Episode: {title}

Content to visualize:
{description[:800]}

DESIGN REQUIREMENTS:
- Dark background (deep navy/charcoal/black) with vibrant accent colors (cyan, orange, electric blue)
- Do NOT include the podcast name or host names
- Include 3-5 KEY STATS or FINDINGS as short text callouts with icons/symbols
- Use clean data visualization elements (simple charts, percentage rings, arrows)
- Modern minimalist infographic style — NOT cluttered
- Text must be LEGIBLE and SPELLED CORRECTLY
- 1024x1024 square format suitable for podcast feed
- Professional, tech-forward aesthetic"""

    return prompt


def _find_anchor_episodes_needing_images(anchor_path):
    """Parse anchor_feed.xml and return episodes using the nologo image.

    Returns a list of dicts with title, description, guid, and the
    XML element reference for later update.
    """
    if not anchor_path.exists():
        return []

    tree = ET.parse(str(anchor_path))
    items = tree.findall(".//item")
    needs_image = []

    for item in items:
        img_el = item.find("{%s}image" % ITUNES_NS)
        href = img_el.get("href", "") if img_el is not None else ""

        if NOLOGO_MARKER not in href and href:
            continue

        title_el = item.find("title")
        title = title_el.text.strip() if (
            title_el is not None and title_el.text) else ""
        desc_el = item.find("description")
        desc = desc_el.text.strip() if (
            desc_el is not None and desc_el.text) else ""
        guid_el = item.find("guid")
        guid = guid_el.text.strip() if (
            guid_el is not None and guid_el.text) else ""

        # Strip HTML tags from description
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()

        needs_image.append({
            "title": title,
            "description": desc,
            "guid": guid,
            "item": item,
            "source": "anchor",
        })

    return needs_image, tree


def _find_db_episodes_needing_images():
    """Find DB episodes with no image_file set.

    Returns a list of dicts with id, title, description, audio_file.
    """
    conn = get_connection()
    init_db(conn)
    episodes = list_podcasts(conn)
    conn.close()

    needs_image = []
    for ep in episodes:
        if ep.get("image_file"):
            continue
        # Skip drafts
        audio = ep.get("audio_file") or ""
        if "/drafts/" in audio:
            continue

        needs_image.append({
            "id": ep["id"],
            "title": ep.get("title", ""),
            "description": ep.get("description", ""),
            "audio_file": audio,
            "source": "db",
        })

    return needs_image


def _generate_one(ep, config, r2_client, dry_run=False):
    """Generate image for one episode, upload to R2, return R2 URL.

    Returns (ep, r2_url) on success, (ep, None) on failure.
    """
    title = ep["title"]
    filename = _stable_filename(title)
    local_path = OUTPUT_DIR / filename

    # Skip if already generated locally
    if local_path.exists():
        r2_key = f"episodes/{filename}"
        if dry_run:
            return (ep, f"(cached) {r2_key}")
        url = upload_file(r2_client, str(local_path), r2_key)
        return (ep, url)

    if dry_run:
        _log("DryRun", f"Would generate: {title[:60]}")
        return (ep, "(dry-run)")

    prompt = _build_prompt(config, title, ep.get("description", ""))
    img_config = config.get("image_generation", {})
    model = img_config.get("model", "gpt-image-1")
    size = img_config.get("size", "1024x1024")
    quality = img_config.get("quality", "medium")

    try:
        result = generate_episode_image(
            prompt, str(local_path), model=model,
            size=size, quality=quality, config=config)
        if not result:
            return (ep, None)
    except Exception as e:
        _log("Error", f"Generation failed for '{title[:50]}': {e}")
        return (ep, None)

    # Upload to R2
    r2_key = f"episodes/{filename}"
    try:
        url = upload_file(r2_client, str(local_path), r2_key)
        return (ep, url)
    except Exception as e:
        _log("Error", f"Upload failed for '{title[:50]}': {e}")
        return (ep, None)


def run_backfill(config, dry_run=False, workers=4):
    """Main entry point for backfilling episode cover art."""
    t0 = time.time()

    anchor_path = Path(__file__).parent / "podcasts" / "anchor_feed.xml"

    # Collect episodes needing images
    anchor_eps, anchor_tree = (
        _find_anchor_episodes_needing_images(anchor_path)
        if anchor_path.exists() else ([], None))
    db_eps = _find_db_episodes_needing_images()

    all_eps = anchor_eps + db_eps
    if not all_eps:
        _log("Done", "all episodes already have custom images")
        return

    _log("Scan",
         f"{len(anchor_eps)} anchor + {len(db_eps)} DB episodes "
         f"need images")

    if dry_run:
        for ep in all_eps:
            _log("DryRun", f"  {ep['title'][:70]}")
        _log("DryRun",
             f"{len(all_eps)} images would be generated")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    r2_client = get_r2_client()

    # Generate and upload in parallel
    succeeded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate_one, ep, config, r2_client): ep
            for ep in all_eps
        }
        for future in as_completed(futures):
            ep, url = future.result()
            if url is None:
                failed += 1
                continue

            succeeded += 1
            title = ep["title"][:50]

            if ep["source"] == "anchor":
                # Update the XML element with new R2 URL
                item = ep["item"]
                img_el = item.find("{%s}image" % ITUNES_NS)
                if img_el is not None:
                    img_el.set("href", url)
                else:
                    ET.SubElement(
                        item, "{%s}image" % ITUNES_NS, href=url)
                _log("Anchor", f"{title}... -> {url}")

            elif ep["source"] == "db":
                # Update DB with local image path
                local_file = str(
                    OUTPUT_DIR / _stable_filename(ep["title"]))
                conn = get_connection()
                init_db(conn)
                update_podcast(conn, ep["id"],
                               image_file=local_file)
                conn.close()
                _log("DB", f"{title}... -> {url}")

    # Write updated anchor_feed.xml
    if anchor_tree and succeeded > 0:
        ET.register_namespace(
            "itunes", ITUNES_NS)
        ET.register_namespace(
            "atom", "http://www.w3.org/2005/Atom")
        ET.register_namespace(
            "dc", "http://purl.org/dc/elements/1.1/")
        ET.register_namespace(
            "content", "http://purl.org/rss/1.0/modules/content/")
        ET.register_namespace(
            "podcast", "https://podcastindex.org/namespace/1.0")
        ET.indent(anchor_tree, space="  ")
        anchor_tree.write(
            str(anchor_path), xml_declaration=True, encoding="UTF-8")
        _log("XML", f"Updated {anchor_path}")

    elapsed = time.time() - t0
    _log("Done",
         f"{succeeded} generated, {failed} failed "
         f"({elapsed:.1f}s)")

    if succeeded > 0:
        _log("Next",
             "run 'make publish-site' to regenerate feed with "
             "new images")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill cover art for episodes without images.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show episodes needing images without generating")
    parser.add_argument(
        "--workers", type=int, default=4,
        help="parallel image generation workers (default: 4)")
    args = parser.parse_args()

    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    run_backfill(config, dry_run=args.dry_run, workers=args.workers)


if __name__ == "__main__":
    main()
