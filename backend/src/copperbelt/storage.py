"""Object storage for original documents and generated artefacts.

Keys are content-addressed and tenant-prefixed: ``{org_id}/documents/{sha256}``.
The local backend is for development and single-node deployments; the S3
backend works with AWS S3, MinIO, Cloudflare R2 and other S3-compatible stores.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol

from copperbelt.config import get_settings


class ObjectStore(Protocol):
    name: str

    def put_file(self, key: str, path: Path, content_type: str) -> None: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def health(self) -> tuple[bool, str]: ...


def _safe_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Unsafe storage key: {key!r}")
    return key


class LocalObjectStore:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError("Storage key escapes the storage root")
        return path

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return  # content-addressed: identical bytes already stored
        fd, tmp = tempfile.mkstemp(dir=dest.parent)
        with os.fdopen(fd, "wb") as out, path.open("rb") as src:
            while chunk := src.read(1024 * 1024):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dest)

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def health(self) -> tuple[bool, str]:
        try:
            probe = self.root / ".health"
            probe.write_text("ok")
            probe.unlink()
            return True, f"local filesystem at {self.root}"
        except OSError as exc:
            return False, f"local storage not writable: {exc}"


class S3ObjectStore:
    name = "s3"

    def __init__(self, bucket: str, endpoint_url: str | None, region: str | None) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("Install the 's3' extra (boto3) to use S3 storage") from exc
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        self.client.upload_file(
            str(path),
            self.bucket,
            _safe_key(key),
            ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
        )

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def health(self) -> tuple[bool, str]:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True, f"s3 bucket {self.bucket}"
        except Exception as exc:  # noqa: BLE001
            return False, f"s3 bucket unreachable: {exc}"


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.storage_backend == "s3":
            if not s.s3_bucket:
                raise RuntimeError("CBI_S3_BUCKET is required when CBI_STORAGE_BACKEND=s3")
            _store = S3ObjectStore(s.s3_bucket, s.s3_endpoint_url, s.s3_region)
        else:
            _store = LocalObjectStore(s.storage_local_root)
    return _store


def reset_store() -> None:
    global _store
    _store = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()
