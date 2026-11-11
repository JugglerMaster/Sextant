from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import deque
from typing import Any

from fastapi import Request

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
MIN_PASSWORD_LENGTH = 12

# Session token hardening. A normally issued cookie is well under 512 bytes;
# these caps only reject deliberately oversized or malformed values before we
# spend memory on base64/JSON parsing.
MAX_SESSION_TOKEN_LENGTH = 4096
MAX_SESSION_PAYLOAD_BYTES = 2048
MAX_SESSION_LIFETIME_SECONDS = 366 * 24 * 60 * 60
SESSION_CLOCK_SKEW_SECONDS = 120

# Deliberately small list of passwords/prefixes that show up in credential
# stuffing lists and the project's own deployment docs.
_COMMON_PASSWORDS = frozenset(
    {
        "admin",
        "admin123",
        "admin1234",
        "administrator",
        "administrator1",
        "changeme",
        "changeme123",
        "letmein",
        "letmein123",
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "qwerty",
        "qwerty123",
        "qwertyuiop",
        "runnercontrol",
        "runnercontrol1",
        "runnercontrol123",
        "welcome",
        "welcome1",
        "welcome123",
    }
)

_SEQUENCE_SOURCES = (
    "0123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "qwertyuiopasdfghjklzxcvbnm",
)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password is required")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$", 5)
        if scheme != "scrypt":
            return False
        expected = _b64d(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64d(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _password_compact(password: str) -> str:
    return "".join(char for char in password.casefold() if char.isalnum())


def _password_is_common(password: str) -> bool:
    lowered = password.casefold()
    compact = _password_compact(password)
    if lowered in _COMMON_PASSWORDS or compact in _COMMON_PASSWORDS:
        return True
    # Treat short numeric/symbol decorations as cosmetic: password!2026,
    # runnercontrol123, changeme-now, ...
    trimmed = compact.rstrip("0123456789")
    return bool(trimmed) and len(compact) - len(trimmed) <= 6 and trimmed in _COMMON_PASSWORDS


def _password_is_repeated(password: str) -> bool:
    if len(password) < 2 or len(set(password)) == 1:
        return True
    # A string is a repetition of a shorter unit exactly when it appears again
    # inside its own doubled form (excluding the trivial first/last copies).
    # This is linear in the password length, unlike trying every period.
    return password in (password + password)[1:-1]


def _password_has_sequence(password: str) -> bool:
    compact = _password_compact(password)
    if len(compact) < 6:
        return False
    for sequence in _SEQUENCE_SOURCES:
        expanded = sequence + sequence
        if compact in expanded or compact[::-1] in expanded:
            return True
    return False


def validate_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.isdigit():
        return "Password must not contain only digits."
    if _password_is_repeated(password):
        return "Password must not be a single repeated pattern."
    if _password_is_common(password):
        return "Choose a less predictable password."
    if _password_has_sequence(password):
        return "Avoid common sequences or keyboard patterns."
    return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def token_matches(token: str, stored_hash: str) -> bool:
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def sign_session(payload: dict[str, Any], secret: bytes) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64e(raw)
    signature = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


def decode_session(value: str | None, secret: bytes) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    if len(value) > MAX_SESSION_TOKEN_LENGTH:
        return None
    body, signature = value.rsplit(".", 1)
    if not body or len(body) > MAX_SESSION_TOKEN_LENGTH:
        return None
    try:
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    except UnicodeEncodeError:
        return None
    try:
        supplied = _b64d(signature)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(supplied, expected):
        return None
    try:
        raw = _b64d(body)
    except (ValueError, TypeError):
        return None
    if len(raw) > MAX_SESSION_PAYLOAD_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    iat = payload.get("iat")
    exp = payload.get("exp")
    if isinstance(iat, bool) or not isinstance(iat, int):
        return None
    if isinstance(exp, bool) or not isinstance(exp, int):
        return None
    now = time.time()
    if iat > now + SESSION_CLOCK_SKEW_SECONDS:
        return None
    lifetime = exp - iat
    if lifetime <= 0 or lifetime > MAX_SESSION_LIFETIME_SECONDS:
        return None
    if exp < now:
        return None
    if not isinstance(payload.get("uid"), int) or not isinstance(payload.get("csrf"), str):
        return None
    if not isinstance(payload.get("sid"), str) or not payload["sid"]:
        return None
    return payload


def make_session_payload(
    user_id: int,
    session_version: int,
    duration_seconds: int,
    session_id: str,
) -> dict[str, Any]:
    return {
        "uid": user_id,
        "sv": session_version,
        "sid": session_id,
        "csrf": new_token(),
        "iat": int(time.time()),
        "exp": int(time.time()) + duration_seconds,
    }


def _parse_ip(value: str) -> str | None:
    """Return a canonical IP address from a header value, or None."""
    value = value.strip()
    if not value:
        return None
    bracketed = value.startswith("[") and "]" in value
    if bracketed:
        value = value[1 : value.index("]")]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    # IPv4 with a port (nginx and some proxies emit this form).
    if not bracketed and value.count(":") == 1:
        host, _, port = value.partition(":")
        if port.isdigit():
            try:
                return str(ipaddress.IPv4Address(host))
            except ValueError:
                return None
    return None


def client_ip(request: Request, trust_proxy: bool = False) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        # Trust the hop appended by the nearest proxy (rightmost entry), not the
        # leftmost value, which may come from the client. Deployments should also
        # configure the proxy to overwrite X-Forwarded-For with $remote_addr.
        for candidate in reversed(forwarded.split(",")):
            parsed = _parse_ip(candidate)
            if parsed:
                return parsed
    return request.client.host if request.client else "unknown"


_DUMMY_PASSWORD_HASH: str | None = None


def verify_password_dummy(password: str) -> None:
    """Spend the same scrypt work for unknown/AD users to reduce timing leaks."""
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))
    verify_password(password, _DUMMY_PASSWORD_HASH)


class LoginRateLimiter:
    """Small bounded in-process limiter.

    Entries are swept once per ``sweep_interval`` and the total number of
    tracked keys is capped at ``max_keys`` (oldest activity is evicted first).
    This keeps a spoofed/rotating client IP or username from growing memory
    without bound. It is still process-local: put a shared limiter in front for
    multi-worker or HA deployments.
    """

    def __init__(
        self,
        attempts: int = 5,
        window_seconds: int = 300,
        lock_seconds: int = 300,
        max_keys: int = 10_000,
        sweep_interval: int = 60,
    ):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds
        self.max_keys = max(1, int(max_keys))
        self.sweep_interval = max(1.0, float(sweep_interval))
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._last_seen: dict[str, float] = {}
        self._last_sweep: float | None = None
        self._lock = threading.Lock()

    def _forget(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
        self._last_seen.pop(key, None)

    def _enforce_limit(self) -> None:
        while len(self._failures) + len(self._locked_until) > self.max_keys:
            if self._last_seen:
                oldest = min(self._last_seen, key=self._last_seen.__getitem__)
            else:  # defensive: never let the bound fail even without timestamps
                oldest = next(iter(self._failures or self._locked_until), None)
                if oldest is None:
                    return
            self._forget(oldest)

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key in list(self._failures):
            bucket = self._failures.get(key)
            if bucket is None:
                continue
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                self._failures.pop(key, None)
        for key, until in list(self._locked_until.items()):
            if until <= now:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)
        for key in list(self._last_seen):
            if key not in self._failures and key not in self._locked_until:
                self._last_seen.pop(key, None)
        self._last_sweep = now
        self._enforce_limit()

    def _maybe_sweep(self, now: float) -> None:
        if self._last_sweep is None:
            self._sweep(now)
            return
        elapsed = now - self._last_sweep
        # Sweep at least once per window as well as per sweep_interval so short
        # windows still release expired buckets promptly.
        if elapsed >= self.sweep_interval or elapsed >= self.window_seconds:
            self._sweep(now)

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            self._maybe_sweep(now)
            until = self._locked_until.get(key, 0)
            if until > now:
                if key in self._last_seen:
                    self._last_seen[key] = now
                return max(1, int(until - now))
            if until:
                self._forget(key)
            return 0

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._maybe_sweep(now)
            bucket = self._failures.get(key)
            if bucket is None:
                bucket = deque()
                self._failures[key] = bucket
            self._last_seen[key] = now
            cutoff = now - self.window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(now)
            if len(bucket) >= self.attempts:
                self._locked_until[key] = now + self.lock_seconds
                self._failures.pop(key, None)
            self._enforce_limit()

    def record_success(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._maybe_sweep(now)
            self._forget(key)
