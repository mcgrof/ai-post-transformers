from pathlib import Path

import pytest

from pdf_utils import _normalize_pdf_url, download_and_extract, download_pdf
from podcast import generate_podcast_from_urls


class DummyResponse:
    def __init__(self, url, body, content_type="application/pdf"):
        self.url = url
        self._body = body
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


def test_normalize_pdf_url_converts_arxiv_abs_and_html_urls():
    assert _normalize_pdf_url("https://arxiv.org/abs/2401.12345") == (
        "https://arxiv.org/pdf/2401.12345.pdf"
    )
    assert _normalize_pdf_url("https://arxiv.org/html/2603.17187v2") == (
        "https://arxiv.org/pdf/2603.17187v2.pdf"
    )
    assert _normalize_pdf_url("https://example.com/paper.pdf") == (
        "https://example.com/paper.pdf"
    )


def test_normalize_pdf_url_converts_openreview_forum_to_pdf():
    # openreview.net/forum?id=X returns HTML, /pdf?id=X returns the PDF
    assert _normalize_pdf_url("https://openreview.net/forum?id=kgzBkyqg6Z") == (
        "https://openreview.net/pdf?id=kgzBkyqg6Z"
    )
    # Trailing query parameters after id should be dropped
    assert _normalize_pdf_url(
        "https://openreview.net/forum?id=abc123&noteId=xyz"
    ) == "https://openreview.net/pdf?id=abc123"
    # id not first in query string still works
    assert _normalize_pdf_url(
        "https://openreview.net/forum?noteId=xyz&id=abc123"
    ) == "https://openreview.net/pdf?id=abc123"
    # Direct pdf URL left alone
    assert _normalize_pdf_url("https://openreview.net/pdf?id=abc123") == (
        "https://openreview.net/pdf?id=abc123"
    )


def test_download_pdf_normalizes_arxiv_abs_before_request(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return DummyResponse(url, b"%PDF-1.5\n1 0 obj\n")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    path = download_pdf("https://arxiv.org/abs/2401.12345")
    try:
        assert seen["url"] == "https://arxiv.org/pdf/2401.12345.pdf"
        assert Path(path).exists()
    finally:
        Path(path).unlink(missing_ok=True)


def test_download_pdf_rejects_non_pdf_response(monkeypatch):
    def fake_get(url, **kwargs):
        return DummyResponse(url, b"<!DOCTYPE html>", "text/html; charset=utf-8")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    with pytest.raises(ValueError, match="did not return a PDF"):
        download_pdf("https://example.com/not-a-pdf")


def test_download_pdf_sends_browser_user_agent(monkeypatch):
    """Many publisher servers (werbos.com 465, others 403) reject
    Python's default urllib UA. We must send a real browser UA on
    every PDF fetch — not just when retrying after an error.
    """
    seen = {}

    def fake_get(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        return DummyResponse(url, b"%PDF-1.5\nbody")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    path = download_pdf("https://example.com/paper.pdf")
    try:
        ua = seen["headers"].get("User-Agent", "")
        assert "Mozilla" in ua, (
            f"download_pdf must send a browser User-Agent; got {ua!r}"
        )
        # Servers that 403 without an Accept header for PDFs are also
        # common; we send Accept: application/pdf as a hint.
        accept = seen["headers"].get("Accept", "")
        assert "application/pdf" in accept
    finally:
        Path(path).unlink(missing_ok=True)


def test_download_and_extract_reads_local_text_sources(tmp_path):
    src = tmp_path / "source.txt"
    src.write_text("Recovered fallback source text.\n", encoding="utf-8")

    text = download_and_extract(str(src))

    assert text == "Recovered fallback source text.\n"


def test_generate_podcast_from_urls_reports_extract_failures_cleanly(
    monkeypatch, capsys
):
    def fail(_url):
        raise ValueError("URL did not return a PDF")

    monkeypatch.setattr("podcast.download_and_extract", fail)

    with pytest.raises(SystemExit) as exc:
        generate_podcast_from_urls(
            ["https://arxiv.org/abs/2401.12345"],
            {"podcast": {}},
        )

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Failed to extract text" in err
    assert "URL did not return a PDF" in err
    assert "Traceback" not in err
