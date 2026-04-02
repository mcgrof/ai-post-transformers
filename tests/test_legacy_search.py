from pathlib import Path

from db import _get_legacy_episode_arxiv_ids


FIXTURE = Path(__file__).parent / "fixtures" / "anchor_feed_minimal.xml"


def test_legacy_feed_arxiv_ids_include_retnet():
    ids = _get_legacy_episode_arxiv_ids(FIXTURE)
    assert '2307.08621' in ids
