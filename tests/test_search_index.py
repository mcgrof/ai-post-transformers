from rss import _build_search_index


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
    assert by_slug["fresh-episode"]["i"].endswith("fresh.webp")
    assert by_slug["fresh-episode"]["l"] is False

    assert by_slug["orphan-legacy-episode"]["t"] == "Orphan Legacy Episode"
    assert by_slug["orphan-legacy-episode"]["d"] == "Legacy"
    assert by_slug["orphan-legacy-episode"]["u"] == "episodes/orphan-legacy-episode/"
    assert by_slug["orphan-legacy-episode"]["l"] is True
