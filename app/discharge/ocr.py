"""Text extraction from uploaded discharge documents.

- Images (png/jpg/...): OCR via the ``tesseract`` binary through pytesseract.
- PDFs: native text extraction via pypdf (no OCR needed for text PDFs).

If tesseract is not installed, image OCR raises :class:`OCRUnavailable` and
the API/UI fall back to the paste-text path -- the demo always works. The
Docker image ships tesseract, so this fallback only bites on bare-metal
dev machines without it.
"""

from __future__ import annotations

import io
import shutil


class OCRUnavailable(Exception):
    """Raised when text cannot be extracted from an upload in this environment."""


def tesseract_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


def extract_text_from_image(data: bytes) -> tuple[str, str]:
    """Return ``(text, method)`` for image bytes. Raises OCRUnavailable."""
    if not tesseract_available():
        raise OCRUnavailable(
            "tesseract is not installed in this environment; "
            "paste the discharge text instead, or run the Docker image.")
    from PIL import Image
    import pytesseract
    image = Image.open(io.BytesIO(data))
    text = pytesseract.image_to_string(image) or ""
    if not text.strip():
        raise OCRUnavailable("OCR produced no text; the image may be blank or unreadable. "
                             "Try pasting the discharge text instead.")
    return text, "tesseract-ocr"


def extract_text_from_pdf(data: bytes) -> tuple[str, str]:
    """Return ``(text, method)`` for PDF bytes. Raises OCRUnavailable."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise OCRUnavailable(f"pypdf is not installed: {exc}")
    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise OCRUnavailable("This PDF has no extractable text (likely a scanned image). "
                             "Convert pages to images or paste the discharge text instead.")
    return text, "pypdf-text"


def sniff_kind(filename: str, content_type: str) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or "pdf" in ctype:
        return "pdf"
    return "image"
