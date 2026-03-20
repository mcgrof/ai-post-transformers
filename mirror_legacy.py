#!/usr/bin/env python3
"""Mirror legacy podcast episodes from Spotify/Anchor CDN to R2.

Scrapes legacy episode HTML pages already on R2, extracts metadata and
audio URLs (hosted on Spotify's CloudFront CDN), downloads the audio,
uploads it to our own R2 bucket, and rebuilds anchor_feed.xml so that
generate_feed() can merge them back into the main RSS feed.

Usage:
    python mirror_legacy.py scrape        # Phase 1: build manifest from HTML pages
    python mirror_legacy.py mirror        # Phase 2: download audio + upload to R2
    python mirror_legacy.py build-feed    # Phase 3: generate anchor_feed.xml
    python mirror_legacy.py all           # Run all phases
"""

import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

LEGACY_SLUGS_PATH = Path("/tmp/r2_legacy_slugs.json")
MANIFEST_PATH = Path(__file__).parent / "legacy_manifest.json"
PROGRESS_PATH = Path("/tmp/legacy_mirror_progress.json")
ANCHOR_FEED_PATH = Path(__file__).parent / "podcasts" / "anchor_feed.xml"
BASE_URL = "https://podcast.do-not-panic.com"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _c(code, text):
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return text
    return f"\033[{code}m{text}\033[0m"


# ---------------------------------------------------------------------------
# Phase 1: Scrape HTML pages to build manifest
# ---------------------------------------------------------------------------

def _fetch_episode_html(slug):
    """Fetch the episode page HTML from R2."""
    url = f"{BASE_URL}/episodes/{slug}/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mirror-legacy/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  {_c('31', 'FAIL')} {slug}: {e}", file=sys.stderr)
        return None


def _parse_episode_html(html, slug):
    """Extract metadata from an episode HTML page."""
    meta = {"slug": slug}

    # Title: <h1>...</h1>
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    meta["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else slug

    # Date: <div class="ep-date">...</div>
    m = re.search(r'class="ep-date"[^>]*>(.*?)</div>', html, re.DOTALL)
    meta["date_str"] = m.group(1).strip() if m else ""

    # Parse date string to ISO format
    if meta["date_str"]:
        try:
            dt = datetime.strptime(meta["date_str"], "%b %d, %Y")
            meta["date_iso"] = dt.strftime("%Y-%m-%d")
        except ValueError:
            try:
                dt = datetime.strptime(meta["date_str"], "%B %d, %Y")
                meta["date_iso"] = dt.strftime("%Y-%m-%d")
            except ValueError:
                meta["date_iso"] = ""
    else:
        meta["date_iso"] = ""

    # Audio URL: <a href="...">...Download...</a>
    # The href wraps a CloudFront URL inside an anchor.fm play URL
    m = re.search(
        r'<a\s+href="([^"]*)"[^>]*>\s*[^<]*[Dd]ownload[^<]*</a>',
        html, re.DOTALL
    )
    if m:
        href = m.group(1)
        # Extract CloudFront URL from anchor.fm wrapper
        cf_match = re.search(r"https%3A%2F%2F[^\s\"']+", href)
        if cf_match:
            meta["audio_url_original"] = urllib.parse.unquote(cf_match.group())
        elif href.startswith("http"):
            meta["audio_url_original"] = href
        else:
            meta["audio_url_original"] = ""
    else:
        meta["audio_url_original"] = ""

    # Determine audio extension
    audio_url = meta.get("audio_url_original", "")
    if ".m4a" in audio_url:
        meta["audio_ext"] = ".m4a"
        meta["audio_type"] = "audio/x-m4a"
    elif ".mp3" in audio_url:
        meta["audio_ext"] = ".mp3"
        meta["audio_type"] = "audio/mpeg"
    else:
        meta["audio_ext"] = ".m4a"
        meta["audio_type"] = "audio/x-m4a"

    # Cover image: <img class="ep-cover" src="...">
    m = re.search(r'class="ep-cover"[^>]*\ssrc="([^"]*)"', html)
    meta["image_url"] = m.group(1) if m else ""

    # Description: <div class="ep-desc">...</div>
    m = re.search(
        r'class="ep-desc"[^>]*>(.*?)</div>\s*(?:<div|</div>)',
        html, re.DOTALL
    )
    if m:
        desc = m.group(1)
        # Remove nested card-sources div
        desc = re.sub(r'<div class="card-sources".*?</div>', "", desc,
                       flags=re.DOTALL)
        # Strip HTML tags but keep text
        desc = re.sub(r"<[^>]+>", "", desc).strip()
        # Collapse whitespace
        desc = re.sub(r"\s+", " ", desc)
        meta["description"] = desc
    else:
        meta["description"] = ""

    # Source URL from card-sources
    m = re.search(r'class="card-sources"[^>]*>.*?href="([^"]*)"', html,
                   re.DOTALL)
    meta["source_url"] = m.group(1) if m else ""

    # R2 audio key and URL (where we'll upload)
    meta["r2_audio_key"] = f"episodes/{slug}{meta['audio_ext']}"
    meta["audio_url_r2"] = f"{BASE_URL}/{meta['r2_audio_key']}"

    return meta


def scrape_manifest():
    """Phase 1: Fetch all legacy episode pages and build metadata manifest."""
    if not LEGACY_SLUGS_PATH.exists():
        print(f"{_c('31', '[Mirror]')} {LEGACY_SLUGS_PATH} not found. "
              "Run the R2 listing first.", file=sys.stderr)
        sys.exit(1)

    slugs = json.loads(LEGACY_SLUGS_PATH.read_text())
    print(f"{_c('36', '[Mirror]')} Scraping {len(slugs)} legacy episode pages...",
          file=sys.stderr)

    manifest = []
    failed = []

    def _process(slug):
        html = _fetch_episode_html(slug)
        if html:
            return _parse_episode_html(html, slug)
        return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_process, s): s for s in slugs}
        for i, future in enumerate(as_completed(futures), 1):
            slug = futures[future]
            try:
                meta = future.result()
                if meta:
                    manifest.append(meta)
                    if i % 50 == 0 or i == len(slugs):
                        print(f"  [{i}/{len(slugs)}] scraped",
                              file=sys.stderr)
                else:
                    failed.append(slug)
            except Exception as e:
                print(f"  {_c('31', 'ERR')} {slug}: {e}", file=sys.stderr)
                failed.append(slug)

    # Sort by date
    manifest.sort(key=lambda m: m.get("date_iso", "") or "9999")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"{_c('32', '[Mirror]')} Manifest saved: {MANIFEST_PATH} "
          f"({len(manifest)} episodes, {len(failed)} failed)",
          file=sys.stderr)

    if failed:
        print(f"  Failed slugs: {failed[:10]}{'...' if len(failed) > 10 else ''}",
              file=sys.stderr)

    # Stats
    has_audio = sum(1 for m in manifest if m.get("audio_url_original"))
    has_image = sum(1 for m in manifest if m.get("image_url"))
    print(f"  Audio URLs found: {has_audio}/{len(manifest)}",
          file=sys.stderr)
    print(f"  Image URLs found: {has_image}/{len(manifest)}",
          file=sys.stderr)

    return manifest


# ---------------------------------------------------------------------------
# Phase 2: Download audio from CloudFront, upload to R2
# ---------------------------------------------------------------------------

def _load_progress():
    if PROGRESS_PATH.exists():
        return set(json.loads(PROGRESS_PATH.read_text()))
    return set()


def _save_progress(done_set):
    PROGRESS_PATH.write_text(json.dumps(sorted(done_set)))


def _mirror_one(ep, r2_client, bucket, done_set):
    """Download audio from CloudFront, upload to R2."""
    slug = ep["slug"]
    audio_url = ep.get("audio_url_original", "")
    r2_key = ep["r2_audio_key"]

    if slug in done_set:
        return "skip"

    if not audio_url:
        return "no-audio"

    # Download to temp file
    try:
        tmp = tempfile.NamedTemporaryFile(
            suffix=ep.get("audio_ext", ".m4a"), delete=False)
        tmp.close()

        req = urllib.request.Request(
            audio_url,
            headers={"User-Agent": "mirror-legacy/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(tmp.name, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    f.write(chunk)

        size_mb = os.path.getsize(tmp.name) / (1024 * 1024)

        # Upload to R2
        content_type = ep.get("audio_type", "audio/x-m4a")
        r2_client.upload_file(
            tmp.name, bucket, r2_key,
            ExtraArgs={"ContentType": content_type})

        return f"ok ({size_mb:.1f}MB)"
    except Exception as e:
        return f"error: {e}"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _mirror_image(ep, r2_client, bucket):
    """Mirror cover image from Spotify CDN to R2 if needed."""
    image_url = ep.get("image_url", "")
    slug = ep["slug"]

    # Skip if already on our R2 or no image
    if not image_url or "podcast.do-not-panic.com" in image_url:
        return None

    # Determine extension from URL
    if ".png" in image_url:
        ext, ct = ".png", "image/png"
    elif ".jpg" in image_url or ".jpeg" in image_url:
        ext, ct = ".jpg", "image/jpeg"
    else:
        ext, ct = ".jpg", "image/jpeg"

    r2_key = f"episodes/{slug}-cover{ext}"

    try:
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.close()
        req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "mirror-legacy/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(tmp.name, "wb") as f:
                f.write(resp.read())
        r2_client.upload_file(
            tmp.name, bucket, r2_key,
            ExtraArgs={"ContentType": ct})
        return f"{BASE_URL}/{r2_key}"
    except Exception as e:
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def mirror_audio():
    """Phase 2: Download audio from CloudFront and upload to R2."""
    if not MANIFEST_PATH.exists():
        print(f"{_c('31', '[Mirror]')} No manifest found. Run 'scrape' first.",
              file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(MANIFEST_PATH.read_text())
    done_set = _load_progress()

    # Get R2 client
    import boto3
    r2_client = boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ.get("R2_BUCKET", "podcast")

    pending = [ep for ep in manifest
               if ep["slug"] not in done_set and ep.get("audio_url_original")]
    print(f"{_c('36', '[Mirror]')} Mirroring audio: {len(pending)} pending "
          f"({len(done_set)} already done, {len(manifest)} total)",
          file=sys.stderr)

    for i, ep in enumerate(pending, 1):
        slug = ep["slug"]
        result = _mirror_one(ep, r2_client, bucket, done_set)
        status = _c('32', result) if result.startswith("ok") else _c('31', result)
        print(f"  [{i}/{len(pending)}] {slug}: {status}", file=sys.stderr)

        if result.startswith("ok"):
            done_set.add(slug)
            # Save progress every 10 episodes
            if i % 10 == 0:
                _save_progress(done_set)

        # Also mirror cover image if on Spotify CDN
        new_img = _mirror_image(ep, r2_client, bucket)
        if new_img:
            ep["image_url_r2"] = new_img

    _save_progress(done_set)

    # Update manifest with R2 image URLs
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))

    total_done = len(done_set)
    print(f"{_c('32', '[Mirror]')} Audio mirroring complete: "
          f"{total_done}/{len(manifest)} episodes on R2",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# Phase 3: Build anchor_feed.xml from manifest
# ---------------------------------------------------------------------------

def _date_to_rfc822(date_iso):
    """Convert YYYY-MM-DD to RFC 822 format for RSS."""
    if not date_iso:
        return ""
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        # RFC 822: "Mon, 01 Jan 2026 12:00:00 +0000"
        return dt.strftime("%a, %d %b %Y 12:00:00 +0000")
    except ValueError:
        return ""


def build_feed():
    """Phase 3: Generate anchor_feed.xml from the manifest."""
    if not MANIFEST_PATH.exists():
        print(f"{_c('31', '[Mirror]')} No manifest found. Run 'scrape' first.",
              file=sys.stderr)
        sys.exit(1)

    manifest = json.loads(MANIFEST_PATH.read_text())

    ET.register_namespace("itunes", ITUNES_NS)

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "AI Post Transformers (legacy)"
    ET.SubElement(channel, "description").text = (
        "Legacy episodes migrated from Spotify/Anchor hosting."
    )
    ET.SubElement(channel, "link").text = BASE_URL

    count = 0
    for ep in manifest:
        title = ep.get("title", "")
        if not title:
            continue

        # Use R2 audio URL if available, otherwise original
        audio_url = ep.get("audio_url_r2", ep.get("audio_url_original", ""))
        if not audio_url:
            continue

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "description").text = ep.get("description", "")

        pub_date = _date_to_rfc822(ep.get("date_iso", ""))
        if pub_date:
            ET.SubElement(item, "pubDate").text = pub_date

        # GUID: use slug for stability
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = f"legacy-{ep['slug']}"

        # Enclosure (audio)
        ET.SubElement(item, "enclosure",
                       url=audio_url,
                       type=ep.get("audio_type", "audio/x-m4a"),
                       length="0")

        # Episode page link
        ET.SubElement(item, "link").text = (
            f"{BASE_URL}/episodes/{ep['slug']}/"
        )

        # Cover image
        image_url = ep.get("image_url_r2", ep.get("image_url", ""))
        if image_url:
            ET.SubElement(item, f"{{{ITUNES_NS}}}image", href=image_url)

        count += 1

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(str(ANCHOR_FEED_PATH), xml_declaration=True, encoding="UTF-8")

    print(f"{_c('32', '[Mirror]')} anchor_feed.xml written: {count} episodes",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "scrape":
        scrape_manifest()
    elif cmd == "mirror":
        mirror_audio()
    elif cmd == "build-feed":
        build_feed()
    elif cmd == "all":
        scrape_manifest()
        mirror_audio()
        build_feed()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
