"""Upload validation without a database: content sniffing, filename hygiene, PDF active-content markers and
the clamd INSTREAM client (run against an in-process fake clamd, so no ClamAV install is needed)."""

from __future__ import annotations

import socket
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from copperbelt.errors import ValidationFailed
from copperbelt.ingestion import validation
from copperbelt.ingestion.validation import ScanResult, check_pdf_safety, clamav_scan, clean_filename, sniff_content_type


@pytest.mark.parametrize(
    ("head", "ctype"),
    [
        (b"%PDF-1.7\n", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n....", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"II*\x00rest", "image/tiff"),
        (b"MM\x00*rest", "image/tiff"),
        ("Rapport géologique, Katanga".encode(), "text/plain"),
    ],
)
def test_content_type_comes_from_the_bytes(tmp_path: Path, head: bytes, ctype: str) -> None:
    f = tmp_path / "misleading.exe"
    f.write_bytes(head)
    assert sniff_content_type(f) == ctype


@pytest.mark.parametrize("head", [b"MZ\x90\x00\x03\x00", b"text with a \x00 null byte", b"\xff\xfe\xfa invalid utf-8"])
def test_unrecognised_or_binary_content_is_rejected(tmp_path: Path, head: bytes) -> None:
    f = tmp_path / "report.pdf"
    f.write_bytes(head)
    with pytest.raises(ValidationFailed) as exc:
        sniff_content_type(f)
    assert exc.value.code == "unsupported_file_type"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\geo\\Carte Kambove (1930).pdf", "Carte Kambove (1930).pdf"),
        ("rapport_été.pdf", "rapport_été.pdf"),
        ("a;rm -rf $HOME.pdf", "a_rm -rf _HOME.pdf"),
        ("", "upload"),
        (None, "upload"),
        ("dir/", "upload"),
    ],
)
def test_filenames_are_reduced_to_a_safe_basename(raw: str | None, clean: str) -> None:
    assert clean_filename(raw) == clean


def test_filenames_are_capped_at_200_characters() -> None:
    assert len(clean_filename("x" * 500 + ".pdf")) == 200


def test_pdf_active_content_is_flagged(tmp_path: Path) -> None:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> /Launch /EmbeddedFile >>")
    assert check_pdf_safety(f) == ["embedded JavaScript", "launch action", "embedded file"]


def test_plain_pdf_has_no_warnings(tmp_path: Path) -> None:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\n%%EOF")
    assert check_pdf_safety(f) == []


class FakeClamd:
    """Speaks the server side of clamd's zINSTREAM protocol and records the bytes it was sent."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.received = b""
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read_exact(self, conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            if not part:
                raise ConnectionError("client closed early")
            buf += part
        return buf

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        with conn:
            assert self._read_exact(conn, len(b"zINSTREAM\0")) == b"zINSTREAM\0"
            while True:
                (size,) = struct.unpack("!L", self._read_exact(conn, 4))
                if size == 0:
                    break
                self.received += self._read_exact(conn, size)
            conn.sendall(self.reply)

    def close(self) -> None:
        self.thread.join(timeout=5)
        self.sock.close()


@pytest.fixture
def clamd(monkeypatch: pytest.MonkeyPatch):
    servers: list[FakeClamd] = []

    def start(reply: bytes) -> FakeClamd:
        server = FakeClamd(reply)
        servers.append(server)
        monkeypatch.setattr(validation, "get_settings", lambda: SimpleNamespace(clamav_host="127.0.0.1", clamav_port=server.port))
        return server

    yield start
    for s in servers:
        s.close()


def test_clean_file_streams_every_byte_and_reports_clean(tmp_path: Path, clamd) -> None:
    data = bytes(range(256)) * 600  # ~150 KB: more than one 64 KB chunk
    f = tmp_path / "scan.bin"
    f.write_bytes(data)
    server = clamd(b"stream: OK\0")
    assert clamav_scan(f) == (ScanResult.clean, None)
    server.close()
    assert server.received == data


def test_infected_file_reports_the_signature(tmp_path: Path, clamd) -> None:
    f = tmp_path / "eicar.txt"
    f.write_bytes(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
    clamd(b"stream: Eicar-Test-Signature FOUND\0")
    assert clamav_scan(f) == (ScanResult.infected, "Eicar-Test-Signature")


def test_scanner_error_reply_is_not_treated_as_clean(tmp_path: Path, clamd) -> None:
    f = tmp_path / "big.bin"
    f.write_bytes(b"x")
    clamd(b"INSTREAM size limit exceeded. ERROR\0")
    assert clamav_scan(f) == (ScanResult.unavailable, "INSTREAM size limit exceeded. ERROR")


def test_unreachable_scanner_is_reported_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.create_server(("127.0.0.1", 0)) as s:
        free_port = s.getsockname()[1]
    monkeypatch.setattr(validation, "get_settings", lambda: SimpleNamespace(clamav_host="127.0.0.1", clamav_port=free_port))
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    status, detail = clamav_scan(f)
    assert status == ScanResult.unavailable
    assert detail


def test_scanning_is_reported_not_configured_without_a_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validation, "get_settings", lambda: SimpleNamespace(clamav_host=None, clamav_port=3310))
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert clamav_scan(f) == (ScanResult.not_configured, "CBI_CLAMAV_HOST not set")
