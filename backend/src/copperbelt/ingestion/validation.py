"""Upload validation: content sniffing, size limits, filename hygiene and malware scanning."""

from __future__ import annotations

import re
import socket
import struct
from dataclasses import dataclass
from pathlib import Path

from copperbelt.config import get_settings
from copperbelt.errors import ValidationFailed

# Content type is decided from the bytes, never from the client's header or extension.
SIGNATURES: list[tuple[bytes, int, str]] = [
    (b"%PDF-", 0, "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"II*\x00", 0, "image/tiff"),
    (b"MM\x00*", 0, "image/tiff"),
]
SUPPORTED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/tiff", "text/plain"}


@dataclass
class SniffResult:
    content_type: str


def sniff_content_type(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(4096)
    for sig, offset, ctype in SIGNATURES:
        if head[offset : offset + len(sig)] == sig:
            return ctype
    try:
        head.decode("utf-8")
        if b"\x00" not in head:
            return "text/plain"
    except UnicodeDecodeError:
        pass
    raise ValidationFailed(
        "Unsupported or unrecognised file type. Supported: PDF, PNG, JPEG, TIFF, UTF-8 text.",
        code="unsupported_file_type",
    )


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._ \-()àâçéèêëîïôûùüÿñæœÀÂÇÉÈÊËÎÏÔÛÙÜŸÑÆŒ]")


def clean_filename(name: str | None) -> str:
    name = (name or "upload").replace("\\", "/").split("/")[-1].strip()
    name = _FILENAME_SAFE.sub("_", name)[:200]
    return name or "upload"


def check_pdf_safety(path: Path) -> list[str]:
    """Return warnings for active-content markers in a PDF. We never execute PDF content,
    but flag JavaScript/launch actions so reviewers know the original is suspicious."""
    warnings = []
    with path.open("rb") as f:
        data = f.read(8 * 1024 * 1024)
    for marker, label in [(b"/JavaScript", "embedded JavaScript"), (b"/Launch", "launch action"), (b"/EmbeddedFile", "embedded file")]:
        if marker in data:
            warnings.append(label)
    return warnings


class ScanResult:
    clean = "clean"
    infected = "infected"
    unavailable = "scanner_unavailable"
    not_configured = "not_configured"


def clamav_scan(path: Path) -> tuple[str, str | None]:
    """Scan with clamd's INSTREAM protocol. Returns (status, signature_or_detail)."""
    s = get_settings()
    if not s.clamav_host:
        return ScanResult.not_configured, "CBI_CLAMAV_HOST not set"
    try:
        with socket.create_connection((s.clamav_host, s.clamav_port), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            with path.open("rb") as f:
                while chunk := f.read(64 * 1024):
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                reply += part
    except OSError as exc:
        return ScanResult.unavailable, str(exc)
    text = reply.rstrip(b"\0").decode(errors="replace")
    if text.endswith("OK"):
        return ScanResult.clean, None
    if "FOUND" in text:
        return ScanResult.infected, text.split(":", 1)[-1].replace("FOUND", "").strip()
    return ScanResult.unavailable, text
