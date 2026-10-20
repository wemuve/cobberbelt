"""Test fixture builders.

ALL CONTENT HERE IS SYNTHETIC. It is written to exercise the pipeline (French and
English text, DMS coordinates, commodity terms, a scanned page) and describes no
real occurrence. Every generated document carries the marker "SYNTHETIC TEST FIXTURE".
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

SYNTHETIC_MARKER = "SYNTHETIC TEST FIXTURE"

FRENCH_PAGE = [
    f"{SYNTHETIC_MARKER} - Rapport de prospection fictif.",
    "Le gisement de cuivre de Kambove Fictif est situe a 10°48' S, 26°36' E dans le Katanga.",
    "Les indices de malachite et de heterogenite indiquent une mineralisation cuivre-cobalt.",
    "La serie des Mines du Roan affleure sur deux kilometres le long de la riviere.",
    "Le sondage DDH-07 a recoupe 12 m de minerai a chalcopyrite.",
]
ENGLISH_PAGE = [
    f"{SYNTHETIC_MARKER} - fictional exploration summary.",
    "A later survey reported that the copper occurrence at Kambove Fictif lies at 10°49' S, 26°35' E.",
    "Grab samples returned 2.1% Cu and 0.3% Co according to the historical assay sheet.",
    "No independent verification of these results has been made.",
]


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_text_pdf(pages: list[list[str]]) -> bytes:
    """Build a minimal valid PDF with a real text layer (Helvetica, WinAnsi)."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, lines in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode()
        )
        ops = ["BT", "/F1 11 Tf", "50 790 Td", "14 TL"]
        for line in lines:
            ops.append(f"({_pdf_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("cp1252")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def _font(size: int) -> ImageFont.ImageFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make_scanned_image(lines: list[str], *, noise: bool = True) -> Image.Image:
    """Render text to a greyscale 'scan' (no text layer) to exercise OCR."""
    img = Image.new("L", (2000, 200 + 80 * len(lines)), color=250)
    draw = ImageDraw.Draw(img)
    font = _font(40)
    for i, line in enumerate(lines):
        draw.text((80, 80 + i * 80), line, fill=20, font=font)
    if noise:
        import random

        rnd = random.Random(7)
        for _ in range(3000):
            img.putpixel((rnd.randrange(img.width), rnd.randrange(img.height)), rnd.randrange(150, 230))
    return img


def make_scanned_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    make_scanned_image(lines).convert("RGB").save(buf, format="PDF", resolution=200)
    return buf.getvalue()


def make_png(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    make_scanned_image(lines).save(buf, format="PNG")
    return buf.getvalue()
