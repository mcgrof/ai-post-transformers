"""PDF download and text extraction utilities."""

import re
import sys
import tempfile
from pathlib import Path

import requests
from pypdf import PdfReader

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}


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
    try:
        resp = requests.get(resolved_url, timeout=timeout, stream=True)
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


def extract_text(pdf_path):
    """Extract text from a PDF file using pypdf.

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

    full_text = "\n\n".join(pages_text)
    if not full_text.strip():
        print(f"[PDF] Warning: No text extracted from {pdf_path} "
              f"(may be image-based)", file=sys.stderr)
    else:
        print(f"[PDF] Extracted {len(full_text)} chars from {len(reader.pages)} pages",
              file=sys.stderr)
    return full_text


def download_and_extract(url):
    """Download a PDF from a URL (or read a local path) and extract its text.

    Local `.txt` / `.md` files are treated as already-extracted source text.

    Args:
        url: URL pointing to a PDF file, or a local filesystem path.

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
    pdf_path = download_pdf(url)
    try:
        return extract_text(pdf_path)
    finally:
        pdf_path.unlink(missing_ok=True)
