"""Page-level text extraction: PDF text layer first, Tesseract OCR when a page has no usable text."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from copperbelt.config import get_settings

# Bump when extraction behaviour changes; stored on every page so results stay comparable.
EXTRACTOR_VERSION = "extract-1.0.0"


@dataclass
class PageText:
    page_number: int
    text: str
    method: str  # "text_layer" | "ocr"
    ocr_confidence: float | None
    language: str | None


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"-\n(?=[a-zà-ÿ])", "", text)  # re-join hyphenated line breaks
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def guess_language(text: str) -> str | None:
    """Cheap stop-word based guess between English and French; None when too little text."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if len(words) < 20:
        return None
    fr = sum(w in {"le", "la", "les", "des", "du", "et", "dans", "est", "une", "sur", "par", "au", "aux", "qui"} for w in words)
    en = sum(w in {"the", "and", "of", "in", "is", "to", "with", "for", "by", "on", "are", "from", "which"} for w in words)
    if fr == en == 0:
        return None
    return "fr" if fr > en else "en"


def ocr_image(image: Image.Image, languages: str | None = None) -> tuple[str, float | None]:
    langs = languages or get_settings().ocr_languages
    data = pytesseract.image_to_data(image.convert("L"), lang=langs, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(data["conf"], data["text"], strict=False) if str(w).strip() and float(c) >= 0]
    text = pytesseract.image_to_string(image.convert("L"), lang=langs)
    return _clean(text), (round(sum(confs) / len(confs), 2) if confs else None)


def extract_pdf(path: Path, *, force_ocr: bool = False) -> Iterator[PageText]:
    settings = get_settings()
    pdf = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            text = ""
            if not force_ocr:
                textpage = page.get_textpage()
                text = _clean(textpage.get_text_range())
                textpage.close()
            if not force_ocr and len(text) >= settings.ocr_min_text_chars:
                yield PageText(index + 1, text, "text_layer", None, guess_language(text))
            else:
                bitmap = page.render(scale=settings.ocr_dpi / 72)
                image = bitmap.to_pil()
                ocr_text, conf = ocr_image(image)
                yield PageText(index + 1, ocr_text, "ocr", conf, guess_language(ocr_text))
            page.close()
    finally:
        pdf.close()


def pdf_page_count(path: Path) -> int:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def extract_image(path: Path) -> Iterator[PageText]:
    with Image.open(path) as img:
        frame = 0
        while True:
            text, conf = ocr_image(img.copy())
            yield PageText(frame + 1, text, "ocr", conf, guess_language(text))
            frame += 1
            try:
                img.seek(frame)  # multi-page TIFF
            except EOFError:
                break


def extract_text_file(path: Path) -> Iterator[PageText]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Form-feed separates pages in plain-text exports; otherwise the file is one page.
    for i, chunk in enumerate(raw.split("\f")):
        text = _clean(chunk)
        yield PageText(i + 1, text, "text_layer", None, guess_language(text))


def extract(path: Path, content_type: str, *, force_ocr: bool = False) -> Iterator[PageText]:
    if content_type == "application/pdf":
        return extract_pdf(path, force_ocr=force_ocr)
    if content_type.startswith("image/"):
        return extract_image(path)
    if content_type == "text/plain":
        return extract_text_file(path)
    raise ValueError(f"No extractor for {content_type}")


def render_pdf_page_png(fileobj: io.BufferedIOBase | bytes, page_number: int, scale: float = 1.5) -> bytes:
    data = fileobj if isinstance(fileobj, bytes) else fileobj.read()
    pdf = pdfium.PdfDocument(data)
    try:
        if not 1 <= page_number <= len(pdf):
            raise IndexError(page_number)
        img = pdf[page_number - 1].render(scale=scale).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()
