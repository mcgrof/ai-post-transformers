#!/usr/bin/env python3
"""Repair malformed Google Scholar links in episode descriptions.

Older descriptions built their "Sources:" Scholar links with a naive
``title.replace(" ", "+")`` that never percent-encoded colons,
ampersands, slashes, brackets, or non-ASCII characters. An unencoded
``&`` even truncated the query so Scholar only searched the text before
it. This tool re-encodes every such URL in place, faithfully (it never
changes the title a URL searches for) and idempotently.

Targets (any combination):
    --db [--range A-B | --all]   papers.db podcasts.description rows
    --drafts [DIR]               local draft sidecar JSONs (default drafts/)
    --manifest FILE              an admin manifest.json (deep-walked)
    --anchor FILE                legacy podcasts/anchor_feed.xml (inline)

Verification flow the task asks for:
    # preview a few drafts, then apply to all
    fix_scholar_urls.py --drafts --dry-run --limit 3
    fix_scholar_urls.py --drafts

Every write makes a timestamped ``.bak`` (files) or a ``.backup`` copy
(sqlite). ``--dry-run`` writes nothing and just reports.
"""

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scholar_urls import (  # noqa: E402
    repair_description,
    repair_inline_urls,
    validate_description_urls,
)


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_file(path):
    dst = f"{path}.bak.{_stamp()}"
    shutil.copy2(path, dst)
    return dst


def _first_diff_line(old, new):
    """Return a short before/after sample of the first changed line."""
    for o, n in zip(old.split("\n"), new.split("\n")):
        if o != n and "scholar.google" in o:
            return o.strip(), n.strip()
    return None, None


# --------------------------------------------------------------------------
# papers.db
# --------------------------------------------------------------------------
def fix_db(db_path, id_range, dry_run, limit, verbose):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    where, params = "description LIKE '%scholar.google%'", []
    if id_range:
        where += " AND id BETWEEN ? AND ?"
        params = list(id_range)
    rows = conn.execute(
        f"SELECT id, title, description FROM podcasts WHERE {where} ORDER BY id",
        params,
    ).fetchall()

    changed = []
    for r in rows:
        new_desc, n = repair_description(r["description"])
        if n and new_desc != r["description"]:
            changed.append((r["id"], r["title"], r["description"], new_desc, n))
            if limit and len(changed) >= limit:
                break

    total_urls = sum(c[4] for c in changed)
    print(f"[db] {db_path}: {len(rows)} scholar-bearing rows scanned, "
          f"{len(changed)} rows need repair ({total_urls} URLs)"
          + (f" [limited to {limit}]" if limit else ""))
    for cid, title, old, new, n in changed:
        o, nw = _first_diff_line(old, new)
        print(f"    id={cid} ({n} urls) {title[:50]}")
        if verbose and o:
            print(f"        - {o}")
            print(f"        + {nw}")

    if not dry_run and changed:
        backup = f"{db_path}.backup.{_stamp()}"
        shutil.copy2(db_path, backup)
        print(f"[db] backed up -> {backup}")
        for cid, _t, _o, new, _n in changed:
            conn.execute("UPDATE podcasts SET description=? WHERE id=?",
                         (new, cid))
        conn.commit()
        print(f"[db] committed {len(changed)} rows")
    conn.close()
    return len(changed), total_urls


# --------------------------------------------------------------------------
# draft sidecar JSONs
# --------------------------------------------------------------------------
def fix_drafts(drafts_dir, dry_run, limit, verbose):
    files = sorted(glob.glob(os.path.join(drafts_dir, "**", "*.json"),
                             recursive=True))
    changed = []
    for f in files:
        try:
            data = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"    !! skip {f}: {exc}", file=sys.stderr)
            continue
        desc = data.get("description")
        if not isinstance(desc, str) or "scholar.google" not in desc:
            continue
        new_desc, n = repair_description(desc)
        if n and new_desc != desc:
            changed.append((f, data, desc, new_desc, n))
            if limit and len(changed) >= limit:
                break

    total_urls = sum(c[4] for c in changed)
    print(f"[drafts] {drafts_dir}: {len(files)} JSONs scanned, "
          f"{len(changed)} need repair ({total_urls} URLs)"
          + (f" [limited to {limit}]" if limit else ""))
    for f, _data, old, new, n in changed:
        o, nw = _first_diff_line(old, new)
        print(f"    {os.path.basename(f)} ({n} urls)")
        if verbose and o:
            print(f"        - {o}")
            print(f"        + {nw}")

    if not dry_run and changed:
        for f, data, _old, new, _n in changed:
            _backup_file(f)
            data["description"] = new
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"[drafts] wrote {len(changed)} files (.bak saved)")
    return len(changed), total_urls


# --------------------------------------------------------------------------
# manifest.json (deep-walk every string value)
# --------------------------------------------------------------------------
def _walk_repair(obj):
    """Repair scholar URLs in every string within a nested structure."""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and "scholar.google" in v:
                new, c = repair_description(v)
                if c == 0:  # flattened variant
                    new, c = repair_inline_urls(v)
                if c:
                    obj[k] = new
                    n += c
            else:
                n += _walk_repair(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and "scholar.google" in v:
                new, c = repair_description(v)
                if c == 0:
                    new, c = repair_inline_urls(v)
                if c:
                    obj[i] = new
                    n += c
            else:
                n += _walk_repair(v)
    return n


def fix_manifest(path, dry_run, verbose):
    data = json.load(open(path, encoding="utf-8"))
    n = _walk_repair(data)
    print(f"[manifest] {path}: {n} URLs repaired")
    if not dry_run and n:
        _backup_file(path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"[manifest] wrote {path} (.bak saved)")
    return (1 if n else 0), n


# --------------------------------------------------------------------------
# anchor_feed.xml (legacy, protected — inline repair, hard invariants)
# --------------------------------------------------------------------------
def fix_anchor(path, dry_run, verbose):
    raw = open(path, encoding="utf-8").read()
    item_before = raw.count("<item>")
    new, n = repair_inline_urls(raw)

    # Protection invariants: never lose an episode, never break the XML,
    # and only ever change scholar q-values.
    if new.count("<item>") != item_before:
        raise SystemExit(
            f"[anchor] ABORT: <item> count changed "
            f"{item_before} -> {new.count('<item>')}")
    import xml.etree.ElementTree as ET
    ET.fromstring(new)  # raises if malformed
    _assert_only_scholar_changed(raw, new)

    print(f"[anchor] {path}: {n} legacy URLs repaired "
          f"({item_before} items preserved, XML valid)")
    if verbose:
        for o, nw in _scholar_diffs(raw, new)[:8]:
            print(f"        - {o}")
            print(f"        + {nw}")
    if not dry_run and n:
        _backup_file(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        print(f"[anchor] wrote {path} (.bak saved)")
    return (1 if n else 0), n


_SCHOLAR_TOKEN = re.compile(r'https://scholar\.google\.com/scholar\?q=[^\s<"]+')


def _assert_only_scholar_changed(old, new):
    """Guarantee the two texts differ only inside scholar URL tokens."""
    old_skeleton = _SCHOLAR_TOKEN.sub("\x00SCHOLAR\x00", old)
    new_skeleton = _SCHOLAR_TOKEN.sub("\x00SCHOLAR\x00", new)
    if old_skeleton != new_skeleton:
        raise SystemExit("[anchor] ABORT: non-scholar text changed")


def _scholar_diffs(old, new):
    olds = _SCHOLAR_TOKEN.findall(old)
    news = _SCHOLAR_TOKEN.findall(new)
    return [(o, n) for o, n in zip(olds, news) if o != n]


def validate_all(db_path, drafts_dir, anchor):
    """Audit every stored description; exit non-zero if any URL is bad.

    A proactive pre-publish gate: run this to prove no malformed Scholar
    link is about to ship. Returns the total count of malformed URLs.
    """
    bad_total = 0

    conn = sqlite3.connect(db_path)
    for cid, desc in conn.execute(
            "SELECT id, description FROM podcasts "
            "WHERE description LIKE '%scholar.google%'"):
        bad = validate_description_urls(desc)
        if bad:
            bad_total += len(bad)
            print(f"    [db id={cid}] {len(bad)} malformed:")
            for u in bad[:3]:
                print(f"        {u}")
    conn.close()

    for f in sorted(glob.glob(os.path.join(drafts_dir, "**", "*.json"),
                              recursive=True)):
        try:
            desc = json.load(open(f, encoding="utf-8")).get("description", "")
        except (json.JSONDecodeError, OSError):
            continue
        bad = validate_description_urls(desc)
        if bad:
            bad_total += len(bad)
            print(f"    [draft {os.path.basename(f)}] {len(bad)} malformed")

    if anchor and os.path.exists(anchor):
        bad = validate_description_urls(open(anchor, encoding="utf-8").read())
        if bad:
            bad_total += len(bad)
            print(f"    [anchor] {len(bad)} malformed")

    if bad_total == 0:
        print("[validate] OK: every Scholar URL is canonically encoded")
    else:
        print(f"[validate] FAIL: {bad_total} malformed Scholar URL(s)")
    return bad_total


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", action="store_true",
                    help="repair papers.db podcasts.description")
    ap.add_argument("--db-path", default="papers.db")
    ap.add_argument("--range", metavar="A-B",
                    help="restrict --db to podcast id range A-B")
    ap.add_argument("--all", action="store_true",
                    help="explicit: repair all rows (default when no --range)")
    ap.add_argument("--drafts", nargs="?", const="drafts", default=None,
                    metavar="DIR", help="repair draft sidecar JSONs")
    ap.add_argument("--manifest", metavar="FILE",
                    help="repair a manifest.json")
    ap.add_argument("--anchor", metavar="FILE",
                    help="repair legacy anchor_feed.xml (inline, protected)")
    ap.add_argument("--validate", action="store_true",
                    help="audit only: report malformed URLs, exit 1 if any")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap number of records changed (for verification)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show before/after samples")
    args = ap.parse_args()

    if args.validate:
        bad = validate_all(
            args.db_path,
            args.drafts if args.drafts is not None else "drafts",
            args.anchor,
        )
        sys.exit(1 if bad else 0)

    if not any([args.db, args.drafts is not None, args.manifest, args.anchor]):
        ap.error("choose at least one target: --db / --drafts / --manifest / --anchor")

    id_range = None
    if args.range:
        m = re.match(r"^(\d+)-(\d+)$", args.range)
        if not m:
            ap.error("--range must look like 100-200")
        id_range = (int(m.group(1)), int(m.group(2)))

    if args.dry_run:
        print("=== DRY RUN (no writes) ===")

    grand = 0
    if args.db:
        _, urls = fix_db(args.db_path, id_range, args.dry_run, args.limit,
                         args.verbose)
        grand += urls
    if args.drafts is not None:
        _, urls = fix_drafts(args.drafts, args.dry_run, args.limit,
                             args.verbose)
        grand += urls
    if args.manifest:
        _, urls = fix_manifest(args.manifest, args.dry_run, args.verbose)
        grand += urls
    if args.anchor:
        _, urls = fix_anchor(args.anchor, args.dry_run, args.verbose)
        grand += urls

    print(f"\nTotal URLs {'that would be ' if args.dry_run else ''}repaired: {grand}")


if __name__ == "__main__":
    main()
