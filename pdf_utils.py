"""PDF download and text extraction utilities."""

import sys
import tempfile
from pathlib import Path

import requests
from pypdf import PdfReader


def download_pdf(url, timeout=60):
    """Download a PDF from a URL to a temporary file.

    Args:
        url: URL pointing to a PDF file.
        timeout: Request timeout in seconds.

    Returns:
        Path to the downloaded temporary file.
    """
    print(f"[PDF] Downloading {url}...", file=sys.stderr)
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()

    suffix = ".pdf"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    for chunk in resp.iter_content(chunk_size=8192):
        tmp.write(chunk)
    tmp.close()

    print(f"[PDF] Saved to {tmp.name}", file=sys.stderr)
    return Path(tmp.name)


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
    """Download a PDF from a URL and extract its text.

    Args:
        url: URL pointing to a PDF file.

    Returns:
        Extracted text as a string.
    """
    pdf_path = download_pdf(url)
    try:
        return extract_text(pdf_path)
    finally:
        pdf_path.unlink(missing_ok=True)
