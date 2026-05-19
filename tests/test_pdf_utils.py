from pathlib import Path

import pytest

from pdf_utils import _normalize_pdf_url, download_and_extract, download_pdf
from podcast import generate_podcast_from_urls


class DummyResponse:
    def __init__(self, url, body, content_type="application/pdf"):
        self.url = url
        self._body = body
        self.content = body
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


def test_download_and_extract_falls_back_to_html_when_url_is_not_pdf(
    monkeypatch,
):
    """Some submissions point at HTML article pages (e.g. research
    blog posts) rather than PDFs. download_and_extract must catch the
    "did not return a PDF" failure, refetch the URL as HTML, and
    return the main article text.
    """
    html_body = (
        b"<html><head><title>Two scenarios for global AI leadership</title>"
        b"</head><body>"
        b"<nav>skip this</nav>"
        b"<article>"
        b"<h1>Two scenarios for global AI leadership</h1>"
        b"<p>First paragraph of the article.</p>"
        b"<script>tracker();</script>"
        b"<p>Second paragraph with detail.</p>"
        b"</article>"
        b"<footer>skip this footer</footer>"
        b"</body></html>"
    )

    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "headers": kwargs.get("headers") or {}})
        return DummyResponse(url, html_body, "text/html; charset=utf-8")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    text = download_and_extract("https://www.example.com/research/post")

    # First call is download_pdf (raises), second is download_html_text
    assert len(calls) == 2
    assert "Mozilla" in calls[1]["headers"].get("User-Agent", "")
    assert "Two scenarios for global AI leadership" in text
    assert "First paragraph of the article." in text
    assert "Second paragraph with detail." in text
    # nav/script/footer must be stripped
    assert "skip this" not in text
    assert "tracker" not in text


def test_download_and_extract_raises_on_html_with_no_extractable_text(
    monkeypatch,
):
    """An HTML response that contains no <article>/<main>/block content
    should produce a clear error rather than an empty silent success."""
    html_body = b"<html><body><script>only()</script></body></html>"

    def fake_get(url, **kwargs):
        return DummyResponse(url, html_body, "text/html; charset=utf-8")

    monkeypatch.setattr("pdf_utils.requests.get", fake_get)

    with pytest.raises(ValueError, match="no extractable article text"):
        download_and_extract("https://www.example.com/empty")


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


def test_extract_text_falls_back_to_ocr_when_pypdf_returns_empty(monkeypatch, tmp_path):
    """Older scanned PDFs (e.g. pre-2000 IEEE preprints) yield nothing
    via pypdf because pages are images. extract_text() must invoke
    the pdftoppm + tesseract OCR fallback for these cases. Without
    this, image-based PDF submissions fail with "No text could be
    extracted".
    """
    import subprocess
    from pdf_utils import extract_text

    # Stub pypdf to return zero text per page
    class _Page:
        def extract_text(self):
            return ""

    class _Reader:
        pages = [_Page(), _Page()]

    monkeypatch.setattr("pdf_utils.PdfReader", lambda p: _Reader())
    monkeypatch.setattr("pdf_utils.shutil.which",
                        lambda x: f"/usr/bin/{x}")

    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        # First call is pdftoppm — write fake page PNGs
        if cmd[0] == "pdftoppm":
            prefix = cmd[-1]
            Path(prefix + "-1.png").write_bytes(b"fake-png")
            Path(prefix + "-2.png").write_bytes(b"fake-png")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        # Subsequent calls are tesseract — return OCR text
        if cmd[0] == "tesseract":
            page_num = "1" if "-1" in cmd[1] else "2"
            return subprocess.CompletedProcess(
                cmd, 0,
                f"OCR text from page {page_num}\n",
                ""
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("pdf_utils.subprocess.run", fake_run)

    # Provide a real path so tempfile.mkdtemp succeeds
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    text = extract_text(fake_pdf)

    assert "OCR text from page 1" in text
    assert "OCR text from page 2" in text
    # First call must be pdftoppm; subsequent calls tesseract
    assert captured_cmds[0][0] == "pdftoppm"
    assert any(c[0] == "tesseract" for c in captured_cmds[1:])


def test_extract_text_returns_empty_when_ocr_tools_missing(monkeypatch, tmp_path):
    """If pdftoppm or tesseract aren't on PATH, fall through cleanly
    so the caller sees an empty string instead of a crash."""
    from pdf_utils import extract_text

    class _Page:
        def extract_text(self): return ""

    class _Reader:
        pages = [_Page()]

    monkeypatch.setattr("pdf_utils.PdfReader", lambda p: _Reader())
    monkeypatch.setattr("pdf_utils.shutil.which", lambda x: None)

    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4")

    text = extract_text(fake_pdf)
    assert text == ""
