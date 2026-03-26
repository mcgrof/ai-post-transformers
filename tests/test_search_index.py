from rss import _build_search_index, _format_sources_html, _search_alias_terms


def test_build_search_index_includes_feed_episodes_and_extra_legacy_slugs():
    episodes = [
        {
            "title": "Fresh Episode",
            "date": "Mar 26, 2026",
            "description": "<p>Fast path serving with public metadata.</p>",
            "thumb_url": "https://podcast.do-not-panic.com/thumbs/fresh.webp",
            "image_url": "",
            "slug": "fresh-episode",
        },
        {
            "title": "Legacy Episode Already In Feed",
            "date": "Aug 10, 2025",
            "description": "<p>Anchor-era classic.</p>",
            "thumb_url": "",
            "image_url": "",
            "slug": "legacy-episode-already-in-feed",
        },
    ]

    search_idx = _build_search_index(
        episodes,
        legacy_slugs=["legacy-episode-already-in-feed", "orphan-legacy-episode"],
    )

    by_slug = {item["s"]: item for item in search_idx}

    assert set(by_slug) == {
        "fresh-episode",
        "legacy-episode-already-in-feed",
        "orphan-legacy-episode",
    }
    assert by_slug["fresh-episode"]["t"] == "Fresh Episode"
    assert by_slug["fresh-episode"]["x"] == "Fast path serving with public metadata."
    assert by_slug["fresh-episode"]["q"] == "Fast path serving with public metadata."
    assert by_slug["fresh-episode"]["i"].endswith("fresh.webp")
    assert by_slug["fresh-episode"]["l"] is False

    assert by_slug["orphan-legacy-episode"]["t"] == "Orphan Legacy Episode"
    assert by_slug["orphan-legacy-episode"]["d"] == "Legacy"
    assert by_slug["orphan-legacy-episode"]["u"] == "episodes/orphan-legacy-episode/"
    assert by_slug["orphan-legacy-episode"]["q"] == "Orphan Legacy Episode"
    assert by_slug["orphan-legacy-episode"]["l"] is True


def test_format_sources_html_splits_flattened_sources_blob_into_lines_and_links():
    blob = (
        "Sources: 1. H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models "
        "https://arxiv.org/abs/2306.14048 2. FastGen: High-throughput Text Generation with Parallel Decoding "
        "https://arxiv.org/abs/2303.01843"
    )
    html = _format_sources_html(blob)
    assert 'Sources:' in html
    assert 'H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models' in html
    assert 'FastGen: High-throughput Text Generation with Parallel Decoding' in html
    assert '<a href="https://arxiv.org/abs/2306.14048" target="_blank">https://arxiv.org/abs/2306.14048</a>' in html
    assert '<br>' in html


def test_format_sources_html_splits_flattened_url_only_blob():
    blob = (
        "Sources: https://arxiv.org/html/2502.15734v1"
        "https://arxiv.org/html/2412.15605v1"
        "https://openreview.net/pdf?id=x7NbaU8RSU"
    )
    html = _format_sources_html(blob)
    assert html.count('<a href="https://') == 3
    assert 'https://arxiv.org/html/2502.15734v1https://' not in html


def test_search_alias_terms_add_h2o_variants_and_arxiv_id():
    aliases = _search_alias_terms('H2O: Heavy-Hitter Oracle for Efficient Generative Inference of Large Language Models')
    assert 'h2o' in aliases
    assert 'h20' in aliases
    assert 'heavy-hitter oracle' in aliases
    assert '2306.14048' in aliases
