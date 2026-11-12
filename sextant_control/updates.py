from __future__ import annotations

import json
import os
import platform
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

USER_AGENT = f"sextant/{__version__}"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "x86-64": "x86_64",
    "x86_64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "armv8": "aarch64",
    "i386": "x86",
    "i686": "x86",
    "x86": "x86",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value))
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts[:4])


def sanitize_feed(feed: str) -> str:
    """Return a log-safe feed description without credentials or query tokens."""
    value = (feed or "").strip()
    if not value:
        return ""
    if value.startswith("github:"):
        return value[:200]
    match = re.match(r"^(https?://)([^/?#\s]+)([^?#\s]*)(\?[^\s#]*)?", value)
    if not match:
        return value[:200]
    scheme, authority, path, _query = match.groups()
    authority = authority.rsplit("@", 1)[-1]
    return f"{scheme}{authority}{path}"[:200]


def sanitize_error(value: Any) -> str:
    """Redact feed tokens and credentials from an error string before logging."""
    text = str(value)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)([?&](?:token|access_token|api_key|key|secret)=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"https?://[^\s?]+\?[^\s]*", lambda match: match.group(0).split("?", 1)[0] + "?<redacted>", text)
    text = re.sub(r"https?://[^\s/@]+@[^\s]+", lambda match: match.group(0).split("@", 1)[-1], text)
    return text[:500]


def _normalize_arch(value: str) -> str:
    token = str(value or "").strip().casefold().replace(" ", "")
    return _ARCH_ALIASES.get(token, token)


def _infer_arch(name: str) -> str:
    lowered = name.casefold()
    for token, normalized in _ARCH_ALIASES.items():
        if token in lowered:
            return normalized
    if "universal2" in lowered:
        return "universal2"
    return ""


def _normalize_digest(value: Any) -> str:
    text = str(value or "").strip()
    if text.casefold().startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    if _SHA256_RE.fullmatch(text):
        return text.casefold()
    return ""


def normalize_asset(raw: Any, *, version: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "name": "",
            "size": 0,
            "url": "",
            "sha256": "",
            "arch": "",
            "kind": "",
            "version": str(version or ""),
        }
    name = str(
        raw.get("name")
        or raw.get("filename")
        or raw.get("file")
        or raw.get("artifact_name")
        or ""
    ).strip()[:255]
    url = str(
        raw.get("url")
        or raw.get("browser_download_url")
        or raw.get("download_url")
        or raw.get("artifact_url")
        or ""
    ).strip()[:2000]
    digest = (
        raw.get("sha256")
        or raw.get("digest")
        or raw.get("checksum")
        or raw.get("artifact_sha256")
    )
    sha256 = _normalize_digest(digest)
    try:
        size = int(raw.get("size") or raw.get("artifact_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size < 0:
        size = 0
    arch = _normalize_arch(
        str(raw.get("arch") or raw.get("architecture") or raw.get("platform") or "")
    )
    if not arch and name:
        arch = _infer_arch(name)
    lowered = name.casefold()
    kind = str(raw.get("kind") or raw.get("type") or "").strip().casefold()
    if not kind:
        kind = "wheel" if lowered.endswith(".whl") else "asset"
    return {
        "name": name,
        "size": size,
        "url": url,
        "sha256": sha256,
        "arch": arch,
        "kind": kind,
        "version": str(raw.get("version") or version or "")[:64],
    }


def select_server_asset(
    assets: list[dict[str, Any]] | None,
    version: str,
    arch: str | None = None,
) -> dict[str, Any] | None:
    """Pick the server wheel from a release, preferring the running architecture."""
    if not assets:
        return None
    current_arch = _normalize_arch(arch or platform.machine())
    target_version = str(version or "").lstrip("vV").casefold()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        normalized = normalize_asset(asset, version=version)
        name = str(normalized.get("name") or "").casefold()
        if not normalized.get("url"):
            continue
        if normalized.get("kind") not in {"wheel", "asset"} and not name.endswith(".whl"):
            continue
        if not name.endswith(".whl"):
            continue
        score = 0
        if target_version and target_version in name:
            score += 40
        if "sextant" in name:
            score += 10
        if "agent" in name:
            score -= 5
        if "py3-none-any" in name:
            score += 6
        asset_arch = _normalize_arch(str(normalized.get("arch") or ""))
        if asset_arch:
            if current_arch and asset_arch == current_arch:
                score += 12
            elif current_arch and asset_arch not in {current_arch, "universal2"}:
                score -= 30
        if normalized.get("sha256"):
            score += 3
        if normalized.get("size"):
            score += 1
        candidates.append((score, normalized))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _http_json(url: str, timeout: int = 10, token: str = "") -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
    return json.loads(raw.decode("utf-8"))


def _github_release(repo: str) -> dict[str, Any]:
    token = os.environ.get("SEXTANT_GITHUB_TOKEN", "").strip()
    data = _http_json(f"https://api.github.com/repos/{repo}/releases?per_page=20", token=token)
    if not isinstance(data, list):
        raise ValueError("unexpected GitHub response")
    for release in data:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or release.get("name") or "").strip()
        version = tag.lstrip("vV")
        if not version:
            continue
        assets = [
            normalize_asset(asset, version=version)
            for asset in release.get("assets") or []
            if isinstance(asset, dict)
        ]
        return {
            "version": version,
            "url": str(release.get("html_url", ""))[:1000],
            "notes": str(release.get("body") or "")[:4000],
            "published_at": str(release.get("published_at") or ""),
            "assets": assets,
        }
    raise ValueError("no published release found")


def _static_release(url: str) -> dict[str, Any]:
    data = _http_json(url)
    if not isinstance(data, dict):
        raise ValueError("unexpected update feed")
    version = str(data.get("version", "")).lstrip("vV")
    raw_assets = data.get("assets") or []
    assets = [
        normalize_asset(asset, version=version)
        for asset in raw_assets
        if isinstance(asset, dict)
    ] if isinstance(raw_assets, list) else []
    if not assets:
        if isinstance(data.get("artifact"), dict):
            assets = [normalize_asset(data["artifact"], version=version)]
        # Also accept a single top-level artifact description for small feeds.
        artifact_keys = {
            "artifact_url",
            "artifact_sha256",
            "artifact_size",
            "artifact_name",
            "artifact_arch",
            "artifact_digest",
            "wheel_url",
            "wheel_sha256",
            "wheel_size",
            "wheel_name",
            "download_url",
        }
        if any(key in data for key in artifact_keys):
            assets = [
                normalize_asset(
                    {
                        "name": data.get("artifact_name")
                        or data.get("wheel_name")
                        or data.get("filename")
                        or "",
                        "url": data.get("artifact_url")
                        or data.get("wheel_url")
                        or data.get("download_url")
                        or "",
                        "sha256": data.get("artifact_sha256")
                        or data.get("artifact_digest")
                        or data.get("wheel_sha256")
                        or data.get("sha256"),
                        "size": data.get("artifact_size")
                        or data.get("wheel_size")
                        or data.get("size")
                        or 0,
                        "arch": data.get("artifact_arch") or data.get("arch") or "",
                    },
                    version=version,
                )
            ]
        elif isinstance(data.get("wheel"), dict):
            assets = [normalize_asset(data["wheel"], version=version)]
    return {
        "version": version,
        "url": str(data.get("url", url))[:1000],
        "notes": str(data.get("notes", ""))[:4000],
        "published_at": str(data.get("published_at", "")),
        "assets": assets,
    }


def check_update(
    feed: str,
    current_version: str = __version__,
    *,
    arch: str | None = None,
) -> dict[str, Any]:
    feed = (feed or "").strip()
    result: dict[str, Any] = {
        "enabled": bool(feed),
        "feed": sanitize_feed(feed),
        "feed_kind": "github" if feed.startswith("github:") else ("static" if feed else ""),
        "feed_state": "disabled" if not feed else "ok",
        "current_version": current_version,
        "latest_version": "",
        "update_available": False,
        "url": "",
        "notes": "",
        "published_at": "",
        "assets": [],
        "selected_asset": None,
        "hash_available": False,
        "apply_available": False,
        "apply_disabled_reason": "No update feed is configured.",
        "checked_at": _now(),
        "last_checked": "",
        "error": "",
    }
    if not feed:
        return result
    try:
        if feed.startswith("github:"):
            repo = feed.split(":", 1)[1].strip()
            if not repo:
                raise ValueError("SEXTANT_UPDATE_FEED is missing the owner/repo")
            release = _github_release(repo)
        elif feed.startswith("https://") or feed.startswith("http://"):
            release = _static_release(feed)
        else:
            raise ValueError("SEXTANT_UPDATE_FEED must be github:owner/repo or an https URL")
        result.update(
            {
                "latest_version": release.get("version", ""),
                "url": release.get("url", ""),
                "notes": release.get("notes", ""),
                "published_at": release.get("published_at", ""),
                "assets": release.get("assets") or [],
            }
        )
        result["last_checked"] = result["checked_at"]
        result["update_available"] = parse_version(result["latest_version"]) > parse_version(current_version)
        decorate_release(result, arch=arch)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        result["feed_state"] = "error"
        result["error"] = sanitize_error(f"{type(exc).__name__}: {exc}")
        result["apply_disabled_reason"] = "The update feed could not be read."
    return result


def decorate_release(result: dict[str, Any], *, arch: str | None = None) -> dict[str, Any]:
    """Add selected-asset and apply-availability fields to a check result."""
    assets = result.get("assets") or []
    selected = result.get("selected_asset")
    if not (isinstance(selected, dict) and selected.get("url")):
        selected = select_server_asset(
            assets, str(result.get("latest_version") or ""), arch=arch
        )
    result["selected_asset"] = selected
    result["hash_available"] = bool(selected and selected.get("sha256"))
    if not result.get("enabled"):
        reason = "No update feed is configured."
    elif result.get("error"):
        reason = "The update feed could not be read."
    elif not result.get("update_available"):
        reason = "No newer release is available."
    elif selected is None:
        reason = "The release does not contain a server wheel asset."
    elif not selected.get("url"):
        reason = "The server wheel asset has no download URL."
    elif not selected.get("size"):
        reason = "The release does not publish the server wheel size."
    elif not selected.get("sha256"):
        reason = "The release does not publish a SHA-256 digest for the server wheel."
    else:
        reason = ""
    result["apply_available"] = bool(result.get("update_available") and not reason)
    result["apply_disabled_reason"] = reason
    return result


def decorate_status(
    result: dict[str, Any],
    *,
    check_interval_hours: int,
    state: dict[str, Any] | None = None,
    restart_pending: bool = False,
) -> dict[str, Any]:
    """Return the public status payload used by the admin Updates panel."""
    payload = dict(result)
    payload["check_interval_hours"] = int(check_interval_hours)
    payload["last_checked"] = (
        payload.get("checked_at") or "" if payload.get("enabled") else ""
    )
    payload["feed_state"] = payload.get("feed_state") or (
        "disabled" if not payload.get("enabled") else ("error" if payload.get("error") else "ok")
    )
    decorate_release(payload)
    state = state or {}
    previous = state.get("previous_target") if isinstance(state, dict) else None
    rollback_available = False
    if previous:
        try:
            rollback_available = Path(str(previous)).is_dir()
        except OSError:
            rollback_available = False
    payload["rollback_available"] = rollback_available
    payload["previous_version"] = str(state.get("previous_version") or "") if isinstance(state, dict) else ""
    payload["installed_version"] = str(state.get("version") or "") if isinstance(state, dict) else ""
    payload["restart_pending"] = bool(restart_pending)
    payload["last_update"] = {
        key: state.get(key)
        for key in ("version", "previous_version", "applied_at", "rolled_back_at")
        if isinstance(state, dict) and state.get(key)
    }
    return payload
