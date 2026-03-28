from pathlib import Path

import pytest

from pdf_utils import _normalize_pdf_url, download_pdf
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


def test_download_pdf_normalizes_arxiv_abs_before_request(monkeypatch):
    seen = {}

    def fake_get(url, timeout=60, stream=True):
        seen["url"] = url
        return DummyResponse(url, b"%PDF-1.5\n1 0 obj\n")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    path = download_pdf("https://arxiv.org/abs/2401.12345")
    try:
        assert seen["url"] == "https://arxiv.org/pdf/2401.12345.pdf"
        assert Path(path).exists()
    finally:
        Path(path).unlink(missing_ok=True)


def test_download_pdf_rejects_non_pdf_response(monkeypatch):
    def fake_get(url, timeout=60, stream=True):
        return DummyResponse(url, b"<!DOCTYPE html>", "text/html; charset=utf-8")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    with pytest.raises(ValueError, match="did not return a PDF"):
        download_pdf("https://example.com/not-a-pdf")


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
