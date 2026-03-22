"""Tests for queue ingest configuration."""


def test_arxiv_categories_include_systems_sources(config):
    categories = set(config["arxiv_categories"])

    assert {"cs.DC", "cs.PF", "cs.DB"} <= categories
