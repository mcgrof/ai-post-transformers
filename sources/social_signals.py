"""Social/influencer signal scoring for the paper queue.

Detects papers mentioned by known ML researchers and influencers,
and papers trending on social platforms. Uses multiple strategies:

1. Author matching — boost papers by known influential researchers
2. Papers With Code trending — scrape PWC for trending papers
3. Semantic Scholar recommendations — highly cited recent papers
4. Web search heuristic — search for social mentions of paper titles

All sources are tracked transparently in scoring metadata.
"""

import re
import sys
import time
import requests
from datetime import datetime, timezone


def _log(tag, msg, color="cyan"):
    print(f"{tag} {msg}", file=sys.stderr)


# Curated list of influential ML researchers / public reviewers.
# These are people who publicly discuss and review papers on social
# media, YouTube, blogs, etc. Papers they author OR publicly mention
# get a score boost.
#
# Format: {"name": display_name, "arxiv_names": [name variants on arXiv],
#           "type": "author"|"reviewer"|"both", "weight": float}
DEFAULT_INFLUENCERS = [
    # Prominent researchers who also publicly review/discuss papers
    {"name": "Andrej Karpathy", "arxiv_names": ["Andrej Karpathy", "A. Karpathy"],
     "type": "both", "weight": 1.0,
     "platforms": ["twitter/x", "youtube"]},
    {"name": "Sebastian Raschka", "arxiv_names": ["Sebastian Raschka", "S. Raschka"],
     "type": "both", "weight": 0.9,
     "platforms": ["twitter/x", "substack", "github"]},
    {"name": "Yann LeCun", "arxiv_names": ["Yann LeCun", "Y. LeCun"],
     "type": "both", "weight": 0.95,
     "platforms": ["twitter/x"]},
    {"name": "Ilya Sutskever", "arxiv_names": ["Ilya Sutskever", "I. Sutskever"],
     "type": "author", "weight": 1.0,
     "platforms": []},
    {"name": "Geoffrey Hinton", "arxiv_names": ["Geoffrey Hinton", "G. Hinton", "Geoffrey E. Hinton"],
     "type": "author", "weight": 1.0,
     "platforms": []},
    {"name": "Tri Dao", "arxiv_names": ["Tri Dao", "T. Dao"],
     "type": "author", "weight": 0.95,
     "platforms": ["twitter/x"]},
    {"name": "Noam Shazeer", "arxiv_names": ["Noam Shazeer", "N. Shazeer"],
     "type": "author", "weight": 0.95,
     "platforms": []},
    {"name": "Jason Wei", "arxiv_names": ["Jason Wei", "J. Wei"],
     "type": "both", "weight": 0.85,
     "platforms": ["twitter/x"]},
    {"name": "Sasha Rush", "arxiv_names": ["Alexander Rush", "Alexander M. Rush", "Sasha Rush"],
     "type": "both", "weight": 0.85,
     "platforms": ["twitter/x", "blog"]},
    {"name": "Yannic Kilcher", "arxiv_names": ["Yannic Kilcher"],
     "type": "reviewer", "weight": 0.8,
     "platforms": ["youtube", "twitter/x"]},
    {"name": "Elvis Saravia", "arxiv_names": ["Elvis Saravia"],
     "type": "reviewer", "weight": 0.75,
     "platforms": ["twitter/x", "github"]},

    # Labs / orgs whose papers are high-signal
    {"name": "Kaiming He", "arxiv_names": ["Kaiming He", "K. He"],
     "type": "author", "weight": 0.95,
     "platforms": []},
    {"name": "Ashish Vaswani", "arxiv_names": ["Ashish Vaswani", "A. Vaswani"],
     "type": "author", "weight": 0.9,
     "platforms": []},
    {"name": "Dzmitry Bahdanau", "arxiv_names": ["Dzmitry Bahdanau", "D. Bahdanau"],
     "type": "author", "weight": 0.85,
     "platforms": []},

    # Active paper reviewers / aggregators
    {"name": "AK (Papers With Code)", "arxiv_names": [],
     "type": "reviewer", "weight": 0.7,
     "platforms": ["twitter/x", "papers-with-code"]},
    {"name": "Lilian Weng", "arxiv_names": ["Lilian Weng"],
     "type": "both", "weight": 0.85,
     "platforms": ["blog", "twitter/x"]},
    {"name": "Chip Huyen", "arxiv_names": ["Chip Huyen"],
     "type": "both", "weight": 0.8,
     "platforms": ["twitter/x", "blog"]},

    # Systems / efficiency researchers
    {"name": "Song Han", "arxiv_names": ["Song Han", "S. Han"],
     "type": "author", "weight": 0.9,
     "platforms": ["twitter/x"]},
    {"name": "Trevor Gale", "arxiv_names": ["Trevor Gale", "T. Gale"],
     "type": "author", "weight": 0.8,
     "platforms": []},
    {"name": "Tim Dettmers", "arxiv_names": ["Tim Dettmers", "T. Dettmers"],
     "type": "author", "weight": 0.9,
     "platforms": ["twitter/x", "blog"]},
    {"name": "Elias Frantar", "arxiv_names": ["Elias Frantar", "E. Frantar"],
     "type": "author", "weight": 0.85,
     "platforms": []},
]


def get_influencer_list(config):
    """Get influencer list, extending defaults with any custom entries.

    Config format under social_signals.influencers is a list of dicts
    with the same structure as DEFAULT_INFLUENCERS.

    Custom entries are additive, not a replacement for the defaults.
    If a custom influencer reuses the same normalized name as a default,
    the custom entry wins.
    """
    custom = config.get("social_signals", {}).get("influencers") or []
    if not custom:
        return DEFAULT_INFLUENCERS

    merged = {inf["name"].strip().lower(): inf for inf in DEFAULT_INFLUENCERS}
    for inf in custom:
        name = inf.get("name", "").strip().lower()
        if not name:
            continue
        merged[name] = inf
    return list(merged.values())


def score_author_influence(papers, config):
    """Match paper authors against the influencer list.

    Returns a dict mapping arxiv_id -> {
        "influencer_matches": [{"name": ..., "weight": ..., "type": ...}],
        "influencer_boost": float (0-1),
        "scoring_sources": ["influencer:Name1", "influencer:Name2"]
    }
    """
    influencers = get_influencer_list(config)

    # Build a lookup: normalized_name -> influencer record
    name_lookup = {}
    for inf in influencers:
        for arxiv_name in inf.get("arxiv_names", []):
            name_lookup[arxiv_name.lower()] = inf

    results = {}
    matched_count = 0

    for paper in papers:
        matches = []
        paper_authors = paper.get("authors", [])

        for author in paper_authors:
            author_lower = author.lower()
            # Check exact match
            if author_lower in name_lookup:
                inf = name_lookup[author_lower]
                matches.append({
                    "name": inf["name"],
                    "weight": inf["weight"],
                    "type": inf["type"],
                    "match_type": "author",
                })
            else:
                # Check partial match (last name matching)
                for arxiv_name, inf in name_lookup.items():
                    # Match on last name if it's distinctive enough (>4 chars)
                    last_name = arxiv_name.split()[-1]
                    if len(last_name) > 4 and last_name in author_lower:
                        # Also verify first initial matches
                        first_initial = arxiv_name[0].lower()
                        if author_lower.startswith(first_initial):
                            matches.append({
                                "name": inf["name"],
                                "weight": inf["weight"] * 0.8,  # slight discount for fuzzy
                                "type": inf["type"],
                                "match_type": "author_fuzzy",
                            })
                            break

        if matches:
            matched_count += 1
            # Boost = max weight among matches (don't stack linearly)
            max_weight = max(m["weight"] for m in matches)
            boost = min(1.0, max_weight * 0.25)  # cap at 0.25

            sources = [f"influencer:{m['name']}" for m in matches]
            results[paper["arxiv_id"]] = {
                "influencer_matches": matches,
                "influencer_boost": boost,
                "scoring_sources": sources,
            }

    _log("[Social]", f"Author influence: {matched_count}/{len(papers)} "
         f"papers matched influencers")
    return results


def fetch_pwc_trending():
    """Fetch trending papers from Papers With Code.

    Returns a set of arXiv IDs that appear on the PWC trending page.
    """
    _log("[Social]", "Fetching Papers With Code trending...")
    try:
        resp = requests.get(
            "https://paperswithcode.com/",
            timeout=30,
            headers={"User-Agent": "paper-feed/1.0 (research tool)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        _log("[Social]", f"PWC error: {e}")
        return set()

    # PWC links to arxiv papers
    arxiv_ids = set()
    patterns = [
        re.compile(r'arxiv\.org/abs/(\d{4}\.\d{4,5})'),
        re.compile(r'arxiv\.org/pdf/(\d{4}\.\d{4,5})'),
        re.compile(r'/paper/[^"]*?(\d{4}\.\d{4,5})'),
    ]
    for pattern in patterns:
        for match in pattern.finditer(resp.text):
            arxiv_ids.add(match.group(1))

    _log("[Social]", f"PWC trending: {len(arxiv_ids)} papers")
    return arxiv_ids


def fetch_social_signals(papers, config):
    """Aggregate all social signals for a set of papers.

    Returns a dict mapping arxiv_id -> {
        "influencer_boost": float,
        "pwc_trending": bool,
        "social_score": float (combined),
        "scoring_sources": [str],
    }
    """
    social_cfg = config.get("social_signals", {})
    if not social_cfg.get("enabled", True):
        _log("[Social]", "Social signals disabled in config")
        return {}

    # 1. Author influence matching
    influence = score_author_influence(papers, config)

    # 2. Papers With Code trending
    pwc_ids = set()
    if social_cfg.get("pwc_enabled", True):
        pwc_ids = fetch_pwc_trending()

    # 3. Combine signals
    results = {}
    pwc_boost = social_cfg.get("pwc_boost", 0.10)
    influencer_weight = social_cfg.get("influencer_weight", 1.0)

    for paper in papers:
        aid = paper["arxiv_id"]
        inf_data = influence.get(aid, {})
        is_pwc = aid in pwc_ids

        sources = list(inf_data.get("scoring_sources", []))
        inf_boost = inf_data.get("influencer_boost", 0.0) * influencer_weight

        if is_pwc:
            sources.append("pwc_trending")

        pwc_val = pwc_boost if is_pwc else 0.0

        # Combined social score (don't let it exceed 0.35)
        social_score = min(0.35, inf_boost + pwc_val)

        if sources:
            results[aid] = {
                "influencer_boost": inf_boost,
                "influencer_matches": inf_data.get("influencer_matches", []),
                "pwc_trending": is_pwc,
                "social_score": social_score,
                "scoring_sources": sources,
            }

    matched = len(results)
    _log("[Social]", f"Combined social signals: {matched}/{len(papers)} "
         f"papers have signals")
    return results
