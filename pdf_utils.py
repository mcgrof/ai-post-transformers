"""PDF (and HTML) download and text extraction utilities."""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}
_HTML_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre")


_ARXIV_ABS_RE = re.compile(
    r"https?://(?:www\.)?arxiv\.org/(?:abs|html)/"
    r"(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?(?:[?#].*)?$"
)

_OPENREVIEW_FORUM_RE = re.compile(
    r"https?://openreview\.net/forum\?(.*&)?id=([A-Za-z0-9_-]+)(?:&.*)?$"
)


def _normalize_pdf_url(url):
    """Normalize common paper URLs into direct PDF URLs when possible."""
    raw = url or ""
    match = _ARXIV_ABS_RE.match(raw)
    if match:
        return f"https://arxiv.org/pdf/{match.group(1)}.pdf"
    # OpenReview: /forum?id=X returns HTML, but /pdf?id=X returns PDF.
    match = _OPENREVIEW_FORUM_RE.match(raw)
    if match:
        return f"https://openreview.net/pdf?id={match.group(2)}"
    return raw


# Many publisher and academic servers reject Python's default
# User-Agent (e.g. werbos.com returns 465, others return 403/451).
# Sending a real browser UA fixes the majority of these without
# affecting servers that don't care.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def download_pdf(url, timeout=60):
    """Download a PDF from a URL to a temporary file.

    Args:
        url: URL pointing to a PDF file.
        timeout: Request timeout in seconds.

    Returns:
        Path to the downloaded temporary file.
    """
    resolved_url = _normalize_pdf_url(url)
    if resolved_url != url:
        print(f"[PDF] Normalized {url} -> {resolved_url}", file=sys.stderr)

    print(f"[PDF] Downloading {resolved_url}...", file=sys.stderr)
    headers = {"User-Agent": _BROWSER_UA, "Accept": "application/pdf,*/*"}
    try:
        resp = requests.get(
            resolved_url, timeout=timeout, stream=True, headers=headers,
        )
    except requests.exceptions.SSLError as exc:
        # Many academic servers (university personal pages, preprint
        # mirrors) ship incomplete TLS chains that Python's certifi
        # bundle doesn't trust. These are not MITM signals — they're
        # configuration gaps on hosts like www2.math.uu.se. Fall back
        # once with verification disabled after logging loudly.
        print(
            f"[PDF] TLS verification failed for {resolved_url}: {exc}. "
            "Retrying with verify=False (academic-server fallback).",
            file=sys.stderr,
        )
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        resp = requests.get(
            resolved_url, timeout=timeout, stream=True, verify=False,
            headers=headers,
        )
    resp.raise_for_status()

    content_type = (resp.headers.get("content-type") or "").lower()
    if content_type and "pdf" not in content_type and "octet-stream" not in content_type:
        raise ValueError(
            f"URL did not return a PDF: {resolved_url} "
            f"(content-type: {content_type})"
        )

    suffix = ".pdf"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            tmp.write(chunk)
    tmp.close()

    path = Path(tmp.name)
    with path.open("rb") as fh:
        magic = fh.read(5)
    if magic != b"%PDF-":
        path.unlink(missing_ok=True)
        raise ValueError(f"URL did not return a valid PDF: {resolved_url}")

    print(f"[PDF] Saved to {tmp.name}", file=sys.stderr)
    return path


def sanitize_text(text):
    """Drop lone surrogate codepoints from extracted text.

    pypdf can emit unpaired surrogates from malformed embedded fonts.
    Such strings cannot be UTF-8 encoded, so any downstream file
    write or subprocess pipe raises UnicodeEncodeError ("surrogates
    not allowed"). Sanitize at the source so every consumer gets
    valid text.
    """
    return text.encode("utf-8", errors="replace").decode("utf-8")


def extract_text(pdf_path):
    """Extract text from a PDF file using pypdf, with OCR fallback.

    Older scanned papers (e.g. pre-2000 IEEE / academic preprints)
    are image-based — pypdf returns an empty string. When that
    happens we fall back to rasterizing each page and running
    tesseract OCR. The fallback only kicks in when pypdf can't
    extract anything meaningful, so the fast path stays fast.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Extracted text as a string.
    """
    reader = PdfReader(str(pdf_path))
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    full_text = sanitize_text("\n\n".join(pages_text))
    if full_text.strip():
        print(
            f"[PDF] Extracted {len(full_text)} chars from "
            f"{len(reader.pages)} pages", file=sys.stderr,
        )
        return full_text

    print(
        f"[PDF] No text via pypdf ({len(reader.pages)} pages) — "
        f"trying OCR fallback (image-based PDF)", file=sys.stderr,
    )
    ocr_text = _extract_via_ocr(pdf_path, page_count=len(reader.pages))
    if ocr_text.strip():
        print(
            f"[PDF] OCR extracted {len(ocr_text)} chars", file=sys.stderr,
        )
        return ocr_text

    print(
        f"[PDF] Warning: OCR also returned no text for {pdf_path}",
        file=sys.stderr,
    )
    return ""


def _extract_via_ocr(pdf_path, page_count=None):
    """Rasterize each page of an image-based PDF and run tesseract.

    Requires pdftoppm (poppler-utils) and tesseract on $PATH. Both
    are packaged on the worker host.  Best-effort: if either binary
    is missing or fails, returns "" so the caller can surface a
    clean error.

    Page renders go to a per-PDF tempdir that is unconditionally
    removed on exit so we don't leak hundreds of MB into /tmp.
    """
    if not shutil.which("pdftoppm"):
        print("[PDF] OCR fallback unavailable: pdftoppm not on PATH",
              file=sys.stderr)
        return ""
    if not shutil.which("tesseract"):
        print("[PDF] OCR fallback unavailable: tesseract not on PATH",
              file=sys.stderr)
        return ""

    workdir = Path(tempfile.mkdtemp(prefix="pdf_ocr_"))
    try:
        # 200 DPI is the typical tesseract sweet spot — readable but
        # not so huge that runtime explodes for long papers.
        prefix = str(workdir / "page")
        try:
            subprocess.run(
                ["pdftoppm", "-r", "200", "-png", str(pdf_path), prefix],
                check=True, capture_output=True, timeout=300,
            )
        except subprocess.CalledProcessError as exc:
            print(f"[PDF] pdftoppm failed: {exc.stderr[:200]!r}",
                  file=sys.stderr)
            return ""
        except subprocess.TimeoutExpired:
            print("[PDF] pdftoppm timed out", file=sys.stderr)
            return ""

        page_pngs = sorted(workdir.glob("page*.png"))
        if not page_pngs:
            print("[PDF] pdftoppm produced no pages", file=sys.stderr)
            return ""

        page_texts = []
        for png in page_pngs:
            try:
                proc = subprocess.run(
                    ["tesseract", str(png), "-", "-l", "eng"],
                    check=True, capture_output=True, text=True,
                    timeout=120,
                )
                if proc.stdout.strip():
                    page_texts.append(proc.stdout)
            except subprocess.CalledProcessError as exc:
                print(
                    f"[PDF] tesseract failed for {png.name}: "
                    f"{exc.stderr[:200]!r}", file=sys.stderr,
                )
            except subprocess.TimeoutExpired:
                print(f"[PDF] tesseract timed out for {png.name}",
                      file=sys.stderr)

        return "\n\n".join(page_texts)
    finally:
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except OSError:
            pass


def _extract_html_main_text(html_bytes, *, source_url=""):
    """Pull the main article text out of an HTML page using bs4.

    Drops script/style/nav/header/footer/aside/noscript/iframe/form,
    then prefers <article>, <main>, or <body> as the content root.
    Concatenates heading and block-level text only, so we skip
    navigation links and sidebar boilerplate.
    """
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "noscript", "iframe", "form"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    title = soup.find("title")
    if title and title.get_text(strip=True):
        blocks.append(title.get_text(strip=True))
    for el in root.find_all(_HTML_BLOCK_TAGS):
        text = el.get_text(" ", strip=True)
        if text:
            blocks.append(text)
    extracted = "\n\n".join(blocks)
    print(
        f"[HTML] Extracted {len(extracted)} chars from {source_url}",
        file=sys.stderr,
    )
    return extracted


def download_html_text(url, timeout=60):
    """Download an HTML page and return its main article text.

    Used as a fallback when a submission URL points at an article
    landing page (e.g. a research blog post) rather than a PDF.
    """
    resolved_url = _normalize_pdf_url(url)
    print(f"[HTML] Downloading {resolved_url}...", file=sys.stderr)
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    resp = requests.get(resolved_url, timeout=timeout, headers=headers)
    resp.raise_for_status()
    content_type = (resp.headers.get("content-type") or "").lower()
    if "html" not in content_type and "xml" not in content_type:
        raise ValueError(
            f"URL did not return HTML: {resolved_url} "
            f"(content-type: {content_type})"
        )
    return _extract_html_main_text(resp.content, source_url=resolved_url)


def download_and_extract(url):
    """Download a PDF (or HTML page) and extract its text.

    Local `.txt` / `.md` files are treated as already-extracted source
    text. Remote URLs are tried as PDFs first; if the server returns
    HTML instead, the main article text is extracted via bs4.

    Args:
        url: URL pointing to a PDF or HTML article, or a local
            filesystem path.

    Returns:
        Extracted text as a string.
    """
    local = Path(url).expanduser()
    if local.is_file():
        if local.suffix.lower() in _TEXT_SUFFIXES:
            text = local.read_text(encoding="utf-8")
            print(
                f"[PDF] Loaded {len(text)} chars from text source {local}",
                file=sys.stderr,
            )
            return text
        return extract_text(local)
    try:
        pdf_path = download_pdf(url)
    except ValueError as exc:
        if "did not return a PDF" not in str(exc):
            raise
        text = download_html_text(url)
        if not text.strip():
            raise ValueError(
                f"URL returned HTML but no extractable article text: {url}"
            ) from exc
        return text
    try:
        return extract_text(pdf_path)
    finally:
        pdf_path.unlink(missing_ok=True)
