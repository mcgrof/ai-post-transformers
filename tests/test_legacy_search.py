from pathlib import Path

from db import _get_legacy_episode_arxiv_ids


def test_legacy_feed_arxiv_ids_include_retnet():
    ids = _get_legacy_episode_arxiv_ids(Path('podcasts/anchor_feed.xml'))
    assert '2307.08621' in ids
