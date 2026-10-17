"""
OCR utilities: extract text from uploaded certificates / legal documents.
Supports images (jpg/png) directly and PDFs via pdf2image -> pytesseract.
"""
import os
import hashlib
import logging

import pytesseract
from PIL import Image
from django.conf import settings

logger = logging.getLogger(__name__)

if settings.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD


def compute_sha256(file_path: str) -> str:
    """Compute the SHA-256 hash of a file's raw bytes (this is what gets stored on-chain)."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


# Rasterizing + OCR'ing every page of a PDF at full quality has no natural
# upper bound - a multi-hundred-page legal document would otherwise make a
# single upload take minutes and spike memory (each page is a full 300 DPI
# bitmap). Most of what this app verifies (certificates, marksheets, single
# contracts) is only a handful of pages, so capping keeps registration fast
# without materially hurting the common case; set higher if you routinely
# register long documents and are fine with the extra wait.
PDF_OCR_MAX_PAGES = 15


def _images_from_pdf(file_path: str):
    from pdf2image import convert_from_path
    try:
        return convert_from_path(file_path, dpi=300, first_page=1, last_page=PDF_OCR_MAX_PAGES)
    except Exception as exc:
        logger.warning("pdf2image failed (%s) - is poppler installed?", exc)
        return []


def extract_text(file_path: str) -> str:
    """
    Extract text from a document. Handles PDFs (multi-page, capped at
    PDF_OCR_MAX_PAGES) and common image formats. Returns a single
    concatenated string.
    """
    ext = os.path.splitext(file_path)[1].lower()
    text_chunks = []

    try:
        if ext == '.pdf':
            pages = _images_from_pdf(file_path)
            for i, page_img in enumerate(pages):
                page_text = pytesseract.image_to_string(page_img)
                text_chunks.append(f"--- Page {i + 1} ---\n{page_text}")
        elif ext in ('.png', '.jpg', '.jpeg', '.tiff', '.bmp'):
            img = Image.open(file_path)
            text_chunks.append(pytesseract.image_to_string(img))
        else:
            logger.info("Unsupported file type for OCR: %s", ext)
    except Exception as exc:
        logger.exception("OCR extraction failed for %s: %s", file_path, exc)

    return "\n".join(text_chunks).strip()


def extract_key_fields(text: str) -> dict:
    """
    Very lightweight heuristic field extraction (name / roll no / date / institution)
    from OCR'd certificate text. Real deployments would swap this for a trained
    NER model - kept rule-based here so it works with zero extra dependencies.
    """
    import re

    fields = {}

    date_match = re.search(r'(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})', text)
    if date_match:
        fields['date'] = date_match.group(1)

    roll_match = re.search(r'(roll\s*(no\.?|number)?\s*[:\-]?\s*[A-Za-z0-9\-\/]+)', text, re.IGNORECASE)
    if roll_match:
        fields['roll_no'] = roll_match.group(0)

    cert_id_match = re.search(r'(certificate\s*(no\.?|id|number)?\s*[:\-]?\s*[A-Za-z0-9\-\/]+)', text, re.IGNORECASE)
    if cert_id_match:
        fields['certificate_id'] = cert_id_match.group(0)

    return fields
