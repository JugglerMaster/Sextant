"""Server-side Compose library with immutable revisions and bundles."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

STACK_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
COMPOSE_NAMES = {"compose.yaml", "compose.yml"}
BUILD_RE = re.compile(r"(?m)^\s*build\s*:")
MARKER = Path(".sextant") / "library.json"


class DockerStoreError(ValueError):
    pass


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class ComposeLibrary:
    """Store flat Compose files and immutable revision snapshots under a root."""

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 262144,
        max_files: int = 16,
        max_total_bytes: int = 1_048_576,
        max_bundle_bytes: int = 4_194_304,
    ):
        self.root = Path(root)
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.max_files = max(1, min(int(max_files), 64))
        self.max_total_bytes = max(self.max_file_bytes, int(max_total_bytes))
        self.max_bundle_bytes = max(1024, int(max_bundle_bytes))
        self.stacks_dir = self.root / "stacks"
        self.revisions_dir = self.root / ".sextant" / "revisions"
        self.tmp_dir = self.root / ".sextant" / "tmp"

    # --- root / path safety ----------------------------------------------
    @staticmethod
    def validate_root(path: Path) -> Path:
        path = Path(path).expanduser()
        if not path.is_absolute():
            raise DockerStoreError("Compose directory must be an absolute path.")
        if path == Path("/"):
            raise DockerStoreError("Compose directory cannot be /.")
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                if current.is_symlink():
                    raise DockerStoreError("Compose directory must not contain symlinks.")
            except OSError:
                continue
        resolved = path.resolve(strict=False)
        if resolved == Path("/"):
            raise DockerStoreError("Compose directory cannot be /.")
        if path.exists() and not path.is_dir():
            raise DockerStoreError("Compose directory must be a directory.")
        return resolved

    def ensure(self) -> None:
        self.root = self.validate_root(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.stacks_dir.mkdir(mode=0o700, exist_ok=True)
        self.revisions_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        marker = self.root / MARKER
        if not marker.exists():
            marker.parent.mkdir(mode=0o700, exist_ok=True)
            _write_file(
                marker,
                (json.dumps({"schema": 1, "kind": "sextant-compose-library"}) + "\n").encode("utf-8"),
            )

    def _safe_child(self, base: Path, name: str) -> Path:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise DockerStoreError("Invalid managed file name.")
        base = base.resolve(strict=False)
        child = (base / name).resolve(strict=False)
        if child.parent != base:
            raise DockerStoreError("Path escapes the managed directory.")
        if child.is_symlink():
            raise DockerStoreError("Managed paths must not be symlinks.")
        return child

    def stack_dir(self, slug: str) -> Path:
        if not STACK_SLUG_RE.match(slug):
            raise DockerStoreError("Invalid stack slug.")
        return self._safe_child(self.stacks_dir, slug)

    def revision_dir(self, slug: str, revision_hash: str) -> Path:
        if not STACK_SLUG_RE.match(slug):
            raise DockerStoreError("Invalid stack slug.")
        if not re.match(r"^[a-f0-9]{64}$", revision_hash):
            raise DockerStoreError("Invalid revision hash.")
        slug_dir = self._safe_child(self.revisions_dir, slug)
        return self._safe_child(slug_dir, revision_hash)

    # --- file validation / hashing --------------------------------------
    @staticmethod
    def validate_file_names(names: list[str]) -> None:
        if not names:
            raise DockerStoreError("At least one Compose file is required.")
        found = [name for name in names if name in COMPOSE_NAMES]
        if len(found) != 1:
            raise DockerStoreError("Provide exactly one compose.yaml or compose.yml.")
        for name in names:
            if not FILE_NAME_RE.match(name):
                raise DockerStoreError("Invalid Compose file name.")
            if name in COMPOSE_NAMES:
                continue
            if name == ".env":
                continue
            if name.startswith("."):
                raise DockerStoreError("Only .env may start with a dot.")
            if not name.endswith((".yaml", ".yml")):
                raise DockerStoreError("Only compose.yaml/yml, .env, and YAML overrides are allowed.")
            if name in COMPOSE_NAMES:
                raise DockerStoreError("Duplicate Compose entry point.")

    def validate_files(self, files: dict[str, str | bytes]) -> dict[str, bytes]:
        names = sorted(str(name) for name in files)
        self.validate_file_names(names)
        if len(names) > self.max_files:
            raise DockerStoreError(f"At most {self.max_files} files are allowed.")
        encoded: dict[str, bytes] = {}
        total = 0
        for name in names:
            value = files[name]
            data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
            if not data:
                raise DockerStoreError(f"{name} is empty.")
            if len(data) > self.max_file_bytes:
                raise DockerStoreError(f"{name} exceeds the per-file size limit.")
            total += len(data)
            if total > self.max_total_bytes:
                raise DockerStoreError("Compose file set exceeds the total size limit.")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DockerStoreError(f"{name} must be UTF-8 text.") from exc
            if "\x00" in text:
                raise DockerStoreError(f"{name} contains a NUL byte.")
            if name in COMPOSE_NAMES or name.endswith((".yaml", ".yml")):
                if BUILD_RE.search(text):
                    raise DockerStoreError(
                        "v1 supports image-based Compose only; build: services are not supported."
                    )
            encoded[name] = data
        self.validate_file_names(list(encoded))
        return encoded

    @staticmethod
    def revision_hash(files: dict[str, bytes]) -> str:
        digest = hashlib.sha256()
        for name in sorted(files):
            data = files[name]
            digest.update(name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(str(len(data)).encode("ascii"))
            digest.update(b"\x00")
            digest.update(data)
        return digest.hexdigest()

    @staticmethod
    def manifest(files: dict[str, bytes]) -> list[dict[str, Any]]:
        result = []
        for name in sorted(files):
            data = files[name]
            result.append(
                {
                    "name": name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return result

    # --- read / write ----------------------------------------------------
    def save_revision(self, slug: str, files: dict[str, str | bytes]) -> tuple[str, list[dict[str, Any]]]:
        self.ensure()
        encoded = self.validate_files(files)
        revision_hash = self.revision_hash(encoded)
        revision = self.revision_dir(slug, revision_hash)
        revision.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not revision.exists():
            temp = self.tmp_dir / f"rev-{secrets.token_hex(12)}"
            temp.mkdir(mode=0o700)
            try:
                for name, data in encoded.items():
                    _write_file(self._safe_child(temp, name), data)
                _fsync_dir(temp)
                os.replace(temp, revision)
            finally:
                if temp.exists():
                    shutil.rmtree(temp, ignore_errors=True)
            try:
                revision.chmod(0o700)
            except OSError:
                pass
        else:
            existing = self.read_revision(slug, revision_hash)
            if self.revision_hash(existing) != revision_hash:
                raise DockerStoreError("Existing revision failed hash verification.")
        self._write_current(slug, encoded)
        return revision_hash, self.manifest(encoded)

    def _write_current(self, slug: str, files: dict[str, bytes]) -> None:
        stack = self.stack_dir(slug)
        stack.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            stack.chmod(0o700)
        except OSError:
            pass
        for name, data in files.items():
            _write_file(self._safe_child(stack, name), data)
        for entry in stack.iterdir():
            if entry.name.startswith(".write-"):
                entry.unlink(missing_ok=True)
            elif entry.is_file() and entry.name not in files and not entry.is_symlink():
                entry.unlink(missing_ok=True)
        _fsync_dir(stack)

    def read_revision(self, slug: str, revision_hash: str) -> dict[str, bytes]:
        revision = self.revision_dir(slug, revision_hash)
        if not revision.exists():
            raise DockerStoreError("Revision not found.")
        files: dict[str, bytes] = {}
        for entry in sorted(revision.iterdir()):
            if entry.name.startswith(".write-") or entry.is_symlink() or not entry.is_file():
                continue
            if not FILE_NAME_RE.match(entry.name):
                continue
            files[entry.name] = entry.read_bytes()
        self.validate_file_names(list(files))
        return files

    def read_current(self, slug: str) -> dict[str, bytes]:
        stack = self.stack_dir(slug)
        if not stack.exists():
            raise DockerStoreError("Stack not found.")
        files: dict[str, bytes] = {}
        for entry in sorted(stack.iterdir()):
            if entry.name.startswith(".write-") or entry.is_symlink() or not entry.is_file():
                continue
            files[entry.name] = entry.read_bytes()
        self.validate_file_names(list(files))
        return files

    def delete_stack(self, slug: str) -> None:
        stack = self.stack_dir(slug)
        if stack.exists():
            if stack.is_symlink():
                raise DockerStoreError("Refusing to delete a symlinked stack.")
            shutil.rmtree(stack)
        revisions = self._safe_child(self.revisions_dir, slug)
        if revisions.exists():
            if revisions.is_symlink():
                raise DockerStoreError("Refusing to delete symlinked revisions.")
            shutil.rmtree(revisions)

    # --- bundle ----------------------------------------------------------
    def build_bundle(self, slug: str, revision_hash: str) -> bytes:
        files = self.read_revision(slug, revision_hash)
        if self.revision_hash(files) != revision_hash:
            raise DockerStoreError("Revision hash verification failed.")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=9) as archive:
            for name in sorted(files):
                data = files[name]
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mode = 0o600
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
        data = buffer.getvalue()
        if len(data) > self.max_bundle_bytes:
            raise DockerStoreError("Compose bundle exceeds the size limit.")
        return data

    # --- retention / migration ------------------------------------------
    def prune_revisions(self, slug: str, keep_hashes: set[str]) -> int:
        slug_dir = self._safe_child(self.revisions_dir, slug)
        if not slug_dir.exists():
            return 0
        removed = 0
        for entry in slug_dir.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name in keep_hashes:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        return removed

    def cleanup_temp(self, max_age_seconds: int = 24 * 3600) -> int:
        now = time.time()
        removed = 0
        if not self.tmp_dir.exists():
            return removed
        for entry in self.tmp_dir.iterdir():
            try:
                if now - entry.stat().st_mtime > max_age_seconds:
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed

    def counts(self) -> dict[str, int]:
        stacks = 0
        revisions = 0
        if self.stacks_dir.exists():
            stacks = sum(1 for entry in self.stacks_dir.iterdir() if entry.is_dir() and not entry.is_symlink())
        if self.revisions_dir.exists():
            for stack in self.revisions_dir.iterdir():
                if stack.is_dir() and not stack.is_symlink():
                    revisions += sum(1 for entry in stack.iterdir() if entry.is_dir() and not entry.is_symlink())
        return {"stacks": stacks, "revisions": revisions}

    def migrate_to(self, new_root: Path) -> dict[str, int]:
        self.ensure()
        target = self.validate_root(new_root)
        if target == self.root:
            return {"stacks": 0, "revisions": 0}
        if target.exists():
            entries = list(target.iterdir())
            marker = target / MARKER
            if entries and not marker.exists():
                raise DockerStoreError("Target Compose directory is not empty.")
            if marker.exists() and (target / "stacks").exists():
                raise DockerStoreError("Target already contains a Sextant Compose library.")
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            target.chmod(0o700)
        except OSError:
            pass
        shutil.copytree(self.root, target, dirs_exist_ok=True)
        # Verify every copied revision before switching the active root.
        revisions_root = target / ".sextant" / "revisions"
        stacks = revisions_root.glob("*") if revisions_root.exists() else []
        for stack in stacks:
            if not stack.is_dir() or stack.is_symlink():
                continue
            for revision in stack.iterdir():
                if not revision.is_dir() or revision.is_symlink():
                    continue
                files = {}
                for entry in revision.iterdir():
                    if entry.is_file() and not entry.is_symlink() and not entry.name.startswith(".write-"):
                        files[entry.name] = entry.read_bytes()
                if self.revision_hash(files) != revision.name:
                    raise DockerStoreError("Copied revision failed hash verification.")
        copied = self.counts()
        self.root = target
        self.stacks_dir = self.root / "stacks"
        self.revisions_dir = self.root / ".sextant" / "revisions"
        self.tmp_dir = self.root / ".sextant" / "tmp"
        self.ensure()
        return copied
