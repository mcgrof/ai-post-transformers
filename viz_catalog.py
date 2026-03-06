"""Sync visualization catalogs and link matching episodes.

Fetches visualization catalog JSON files from configured sources,
matches visualizations to podcast episodes via shared arXiv IDs,
and updates episode descriptions with visualization links.
"""

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

from db import (
    get_connection, init_db, get_episodes_by_arxiv_id,
    update_podcast, list_podcasts,
)


def _c(code, text):
    if not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty()):
        return text
    return f"\033[{code}m{text}\033[0m"


def _log(tag, msg):
    print(f"{_c('35', '[Viz]')} {_c('1', tag)}: {msg}", file=sys.stderr)


def run_viz_sync(config):
    """Main entry point: fetch catalogs, match episodes, update descriptions."""
    t0 = time.time()
    viz_config = config.get("visualization", {})
    sources = viz_config.get("sources", [])
    if not sources:
        _log("Skip", "no visualization sources configured")
        return

    cache_dir = viz_config.get("cache_dir", "viz_cache")
    catalogs = fetch_all_catalogs(sources, cache_dir)
    if not catalogs:
        _log("Skip", "no catalogs changed since last sync")
        return

    _log("Fetch", f"{len(catalogs)} catalog(s) with new data")

    conn = get_connection()
    init_db(conn)
    arxiv_to_episodes = get_episodes_by_arxiv_id(conn)

    matches = _match_visualizations(catalogs, arxiv_to_episodes)
    if not matches:
        _log("Match", "no visualizations matched any episodes")
        conn.close()
        return

    _log("Match", f"{len(matches)} visualization-episode link(s) found")

    updated = _update_episode_descriptions(matches, conn)
    conn.close()

    if updated > 0:
        _log("Update", f"{updated} episode description(s) updated")
        from rss import generate_feed
        generate_feed(config)
        _log("RSS", "feed regenerated")
    else:
        _log("Update", "all links already present (no changes)")

    elapsed = time.time() - t0
    _log("Done", f"completed in {elapsed:.1f}s")


def fetch_all_catalogs(sources, cache_dir):
    """Fetch all catalog sources in parallel, returning changed ones.

    Returns a list of (name, catalog_dict) tuples for catalogs
    whose content has changed since the last fetch.
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    catalogs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_fetch_one, src["name"], src["url"], cache_path): src
            for src in sources
        }
        for future in as_completed(futures):
            name, catalog = future.result()
            if catalog is not None:
                catalogs.append((name, catalog))
    return catalogs


def _fetch_one(name, url, cache_dir):
    """Fetch a single catalog, returning (name, catalog_dict) if changed.

    Compares SHA-256 of fetched content against the cached version.
    Returns (name, None) if unchanged or on error.
    """
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "paper-feed/1.0 (viz-sync)"
        })
        resp.raise_for_status()
    except requests.RequestException as e:
        _log(name, f"fetch error: {e}")
        return (name, None)

    content = resp.content
    new_hash = hashlib.sha256(content).hexdigest()

    cache_file = Path(cache_dir) / f"{name}.json"
    if cache_file.exists():
        old_hash = hashlib.sha256(cache_file.read_bytes()).hexdigest()
        if old_hash == new_hash:
            _log(name, "unchanged (cached)")
            return (name, None)

    cache_file.write_bytes(content)
    try:
        catalog = json.loads(content)
    except json.JSONDecodeError as e:
        _log(name, f"JSON parse error: {e}")
        return (name, None)

    _log(name, f"fetched {len(catalog.get('visualizations', []))} "
         f"visualization(s)")
    return (name, catalog)


def _build_viz_url(base_url, viz_url):
    """Resolve a visualization URL against the catalog base_url.

    If viz_url is already absolute, return as-is. Otherwise build
    the full URL from the scheme+host of base_url plus the viz path.
    """
    if viz_url.startswith("http://") or viz_url.startswith("https://"):
        return viz_url
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    viz_url = viz_url.lstrip("/")
    return f"{root}/{viz_url}"


def _match_visualizations(catalogs, arxiv_to_episodes):
    """Find visualization-episode matches via shared arXiv IDs.

    Returns a list of (episode_dict, viz_title, full_url) tuples,
    deduplicated by (episode_id, viz_url).
    """
    matches = []
    seen = set()
    for _name, catalog in catalogs:
        base_url = catalog.get("base_url", "")
        for viz in catalog.get("visualizations", []):
            viz_title = viz.get("title", "Untitled")
            viz_url = _build_viz_url(base_url, viz.get("url", ""))
            arxiv_ids = [
                p["id"] for p in viz.get("papers", [])
                if p.get("type") == "arxiv"
            ]
            for aid in arxiv_ids:
                for ep in arxiv_to_episodes.get(aid, []):
                    key = (ep.get("id"), viz_url)
                    if key not in seen:
                        seen.add(key)
                        matches.append((ep, viz_title, viz_url))
    return matches


def _update_episode_descriptions(matches, conn):
    """Append visualization links to episode descriptions.

    Idempotent: skips episodes that already contain the viz URL.
    Returns the count of episodes actually updated.
    """
    # Group matches by episode ID
    by_episode = {}
    for ep, viz_title, viz_url in matches:
        ep_id = ep.get("id")
        if ep_id not in by_episode:
            by_episode[ep_id] = {"ep": ep, "vizs": []}
        by_episode[ep_id]["vizs"].append((viz_title, viz_url))

    updated = 0
    for ep_id, data in by_episode.items():
        ep = data["ep"]
        desc = ep.get("description") or ""
        changed = False
        for viz_title, viz_url in data["vizs"]:
            if viz_url in desc:
                continue
            desc += f"\n\nInteractive Visualization: {viz_title}\n{viz_url}"
            changed = True
        if changed:
            update_podcast(conn, ep_id, description=desc.strip())
            updated += 1
    return updated
