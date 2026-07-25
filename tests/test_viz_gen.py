"""Regression tests for viz HTML generation guards.

The claude-cli backend used to go agentic on the viz prompt — it tried
to Write the file, hit an approval wall, and returned commentary like
"The Write tool call needs approval", which got saved as the viz page.
_looks_like_html() is the guard that keeps such stray replies off disk.
"""
from viz_gen import _looks_like_html


def test_real_html_passes():
    assert _looks_like_html("<!DOCTYPE html>\n<html><body>x</body></html>")
    assert _looks_like_html("  \n<!doctype HTML><html></html>")


def test_leaked_agent_messages_rejected():
    for junk in [
        "The Write tool call needs approval — since the task just asks...",
        "This viz page already exists and matches the episode content.",
        "Heads up before you approve/deny that write: `viz/foo.html`",
        "I'll skip writing to disk — the request just asks me to return...",
        "```html\n<!DOCTYPE html>",   # fence not stripped / truncated
        "", None,
    ]:
        assert not _looks_like_html(junk), junk


def test_html_without_closing_tag_rejected():
    # A truncated doc that never closes is not shippable.
    assert not _looks_like_html("<!DOCTYPE html>\n<html><body>oops")
