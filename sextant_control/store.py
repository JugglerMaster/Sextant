from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import BinaryIO, Iterator

CHUNK_SIZE = 1024 * 1024


class StoreError(ValueError):
    pass


class ArtifactStore:
    """Content-addressed artifact storage under ``<data>/store``.

    Tier 0: the server computes SHA-256 and size; there is no signature.
    Blobs are immutable and addressed by hash, so identical uploads dedupe.
    """

    def __init__(self, root: Path, max_blob_bytes: int = 512 * 1024 * 1024, max_total_bytes: int = 20 * 1024**3):
        self.root = Path(root)
        self.blob_root = self.root / "blobs" / "sha256"
        self.tmp_dir = self.root / "tmp"
        self.max_blob_bytes = max(1024, int(max_blob_bytes))
        self.max_total_bytes = max(self.max_blob_bytes, int(max_total_bytes))

    def ensure(self) -> None:
        self.blob_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def blob_path(self, sha256: str) -> Path:
        digest = sha256.lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise StoreError("invalid sha256")
        return self.blob_root / digest[:2] / digest

    def new_temp_path(self) -> Path:
        self.ensure()
        return self.tmp_dir / f"upload-{secrets.token_hex(16)}.part"

    def commit(self, temp_path: Path, sha256: str, size: int) -> Path:
        if size <= 0:
            temp_path.unlink(missing_ok=True)
            raise StoreError("empty artifact")
        if size > self.max_blob_bytes:
            temp_path.unlink(missing_ok=True)
            raise StoreError("artifact exceeds the per-file limit")
        destination = self.blob_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            temp_path.unlink(missing_ok=True)
            return destination
        if self.total_bytes() + size > self.max_total_bytes:
            temp_path.unlink(missing_ok=True)
            raise StoreError("store quota exceeded")
        os.replace(temp_path, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        return destination

    def total_bytes(self) -> int:
        total = 0
        if not self.blob_root.exists():
            return 0
        for path in self.blob_root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def delete_blob(self, sha256: str) -> None:
        try:
            self.blob_path(sha256).unlink(missing_ok=True)
        except (OSError, StoreError):
            pass

    def open_blob(self, sha256: str) -> BinaryIO:
        return self.blob_path(sha256).open("rb")

    def iter_blob(self, sha256: str) -> Iterator[bytes]:
        with self.open_blob(sha256) as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    def digest_file(self, path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    def cleanup_temp(self, max_age_seconds: int = 24 * 3600) -> int:
        now = time.time()
        removed = 0
        if not self.tmp_dir.exists():
            return 0
        for path in self.tmp_dir.glob("upload-*.part"):
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed
