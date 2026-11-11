from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from . import __version__

USER_AGENT = f"sextant/{__version__}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value))
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts[:4])


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
            {
                "name": str(asset.get("name", ""))[:255],
                "size": int(asset.get("size") or 0),
                "url": str(asset.get("browser_download_url", "")),
            }
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
    return {
        "version": str(data.get("version", "")).lstrip("vV"),
        "url": str(data.get("url", url))[:1000],
        "notes": str(data.get("notes", ""))[:4000],
        "published_at": str(data.get("published_at", "")),
        "assets": data.get("assets") or [],
    }


def check_update(feed: str, current_version: str = __version__) -> dict[str, Any]:
    feed = (feed or "").strip()
    result: dict[str, Any] = {
        "enabled": bool(feed),
        "feed": feed,
        "current_version": current_version,
        "latest_version": "",
        "update_available": False,
        "url": "",
        "notes": "",
        "published_at": "",
        "assets": [],
        "checked_at": _now(),
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
        result["update_available"] = parse_version(result["latest_version"]) > parse_version(current_version)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result
