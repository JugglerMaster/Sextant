"""Short-lived in-memory store for domain-join passwords.

The command row is persisted with a redacted arg set. The password is attached
only when the agent picks up the command on its next heartbeat and is removed
immediately afterwards. A server restart between queueing and pickup loses the
secret, so no credential is left in SQLite.
"""

from __future__ import annotations

import threading
import time


class DomainSecretStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._items: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def put(self, command_id: str, password: str) -> None:
        if not command_id or not password:
            return
        with self._lock:
            self._items[command_id] = (time.monotonic(), password)
            self._sweep_locked()

    def pop(self, command_id: str) -> str | None:
        with self._lock:
            item = self._items.pop(command_id, None)
        return item[1] if item is not None else None

    def sweep(self) -> None:
        with self._lock:
            self._sweep_locked()

    def _sweep_locked(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        stale = [key for key, (created, _value) in self._items.items() if created < cutoff]
        for key in stale:
            self._items.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
