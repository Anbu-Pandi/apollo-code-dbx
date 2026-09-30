import importlib
import logging
import re
from typing import List, Optional

# Constants used for non-report identification
NON_REPORT_PDF_TEXT_MARKERS = (
    "device registration guide",
    "policy for managing conflicts of interest in relation to investment research",
)
# Research PDFs longer than this page count are assumed to be real reports; the
# NON_REPORT_PDF_TEXT_MARKERS check is only meaningful for short disclosure/registration
# PDFs where the marker dominates the content.
NON_REPORT_PDF_PAGE_COUNT_THRESHOLD = 4


def open_pdf_doc(content: bytes):
    """
    Safely opens a PDF document using fitz (PyMuPDF) from bytes content.
    Returns the document object if successful, None otherwise.
    """
    if not content:
        return None
    try:
        fitz = importlib.import_module("fitz")
    except Exception:
        logging.debug("Could not import fitz (PyMuPDF). PDF parsing disabled.")
        return None
    try:
        return fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        logging.debug(f"Failed to open PDF document: {exc}")
        return None


def extract_pdf_preview_text(content: bytes, max_pages: int = 4) -> str:
    """
    Best-effort extraction of early PDF text for quality checks or NLP tasks.
    Returns the text content in lowercase.
    """
    doc = open_pdf_doc(content)
    if doc is None:
        return ""
    try:
        chunks: List[str] = []
        for idx in range(min(max(int(max_pages), 1), int(doc.page_count or 0))):
            try:
                chunks.append(str(doc.load_page(idx).get_text("text") or ""))
            except Exception:
                continue
        return "\n".join(chunks).lower()
    finally:
        try:
            doc.close()
        except Exception:
            pass


def classify_non_report_payload(content: bytes, extension: str) -> str:
    """
    Checks if a given PDF content looks like a generic boilerplate document (e.g. policy doc)
    or if it has an invalid/HTML payload.
    Returns an empty string if it's a valid report, otherwise a descriptive marker string.
    """
    ext = str(extension or "").strip().lower()
    if ext != ".pdf":
        return ""
    
    raw = bytes(content or b"")
    if not raw:
        return "empty_pdf_payload"
        
    if not raw.lstrip().startswith(b"%PDF-"):
        lower_head = raw[:512].lower()
        if b"<!doctype html" in lower_head or b"<html" in lower_head:
            return "html_saved_with_pdf_extension"
        return "invalid_pdf_magic"
        
    doc = open_pdf_doc(raw)
    if doc is None:
        return ""
        
    try:
        page_count = int(doc.page_count or 0)
        # If the document is long enough, assume it's a real report to avoid false positives 
        # from footer text.
        if page_count > NON_REPORT_PDF_PAGE_COUNT_THRESHOLD:
            return ""
            
        preview_parts: List[str] = []
        for idx in range(min(page_count, NON_REPORT_PDF_PAGE_COUNT_THRESHOLD)):
            try:
                preview_parts.append(str(doc.load_page(idx).get_text("text") or ""))
            except Exception:
                continue
    finally:
        try:
            doc.close()
        except Exception:
            pass
            
    preview = "\n".join(preview_parts).lower()
    if not preview:
        return ""
        
    preview_compact = re.sub(r"\s+", " ", preview)
    for marker in NON_REPORT_PDF_TEXT_MARKERS:
        marker_compact = re.sub(r"\s+", " ", marker)
        if marker in preview or marker_compact in preview_compact:
            return f"non_report_pdf_marker:{marker}"
            
    return ""
