from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__
from .config import AgentConfig


class AgentHTTPError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


MAX_RESPONSE_BYTES = 5_000_000


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the bearer token is never forwarded to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AgentClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.base = config.server_url.rstrip("/")
        self.ssl_context = self._ssl_context()
        handlers: list[Any] = [_NoRedirectHandler()]
        if self.ssl_context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self.ssl_context))
        self._opener = urllib.request.build_opener(*handlers)

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.base.startswith("https://"):
            return None
        if not self.config.verify_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        context = ssl.create_default_context(cafile=self.config.ca_file or None)
        return context

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"sextant-agent/{__version__}",
        }
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout or self.config.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ConnectionError("Server response exceeded the agent response limit.")
                if not raw:
                    return {}
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {"data": parsed}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except (ValueError, AttributeError):
                detail = raw
            raise AgentHTTPError(exc.code, str(detail)[:1000]) from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Cannot reach {self.base}: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise ConnectionError(f"Cannot reach {self.base}: {exc}") from exc

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health", timeout=10)

    def enroll(self, enrollment_token: str, identity: dict[str, Any]) -> dict[str, Any]:
        payload = dict(identity)
        payload["enrollment_token"] = enrollment_token
        return self._request("POST", "/api/agent/enroll", payload, timeout=max(20, self.config.timeout))

    def heartbeat(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/heartbeat",
            snapshot,
            token=self.config.agent_token,
            timeout=max(30, self.config.timeout),
        )

    def send_result(self, command_id: str, result: dict[str, Any]) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        command = urllib.parse.quote(command_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/commands/{command}/result",
            result,
            token=self.config.agent_token,
            timeout=max(30, self.config.timeout),
        )

    def send_output(self, command_id: str, stream: str, seq: int, text: str) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        command = urllib.parse.quote(command_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/commands/{command}/output",
            {"stream": stream, "seq": int(seq), "text": text},
            token=self.config.agent_token,
            timeout=max(15, self.config.timeout),
        )

    def report_discovery(self, scan_id: str, report: dict[str, Any]) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/discovery/report",
            {"scan_id": scan_id, "source": f"agent:{self.config.agent_id}", **report},
            token=self.config.agent_token,
            timeout=max(30, self.config.timeout),
        )

    def upload_docker_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/docker/inventory",
            payload,
            token=self.config.agent_token,
            timeout=max(30, self.config.timeout),
        )

    def console_poll(self, session_id: str, timeout: int = 30) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        session = urllib.parse.quote(session_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/docker/console/{session}/poll",
            {},
            token=self.config.agent_token,
            timeout=max(30, timeout + 5),
        )

    def send_console_output(self, session_id: str, data: bytes) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        session = urllib.parse.quote(session_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/docker/console/{session}/output",
            {"data": base64.b64encode(data).decode("ascii")},
            token=self.config.agent_token,
            timeout=max(20, self.config.timeout),
        )

    def send_console_closed(self, session_id: str, exit_code: int | None, error: str = "") -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        session = urllib.parse.quote(session_id, safe="")
        return self._request(
            "POST",
            f"/api/agent/{agent_id}/docker/console/{session}/closed",
            {"exit_code": exit_code, "error": error[:4000]},
            token=self.config.agent_token,
            timeout=max(20, self.config.timeout),
        )

    def get_update(self, current_version: str) -> dict[str, Any]:
        agent_id = urllib.parse.quote(self.config.agent_id, safe="")
        version = urllib.parse.quote(current_version, safe="")
        return self._request(
            "GET",
            f"/api/agent/{agent_id}/update?current_version={version}",
            token=self.config.agent_token,
            timeout=max(30, self.config.timeout),
        )

    def download_bundle(self, path: str, max_bytes: int = 4 * 1024 * 1024) -> tuple[bytes, dict[str, str]]:
        headers = {
            "Accept": "application/gzip",
            "User-Agent": f"sextant-agent/{__version__}",
        }
        if self.config.agent_token:
            headers["Authorization"] = f"Bearer {self.config.agent_token}"
        request = urllib.request.Request(self.base + path, headers=headers)
        with self._opener.open(request, timeout=max(60, self.config.timeout)) as response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ConnectionError("Docker deployment bundle exceeds the size limit.")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            return data, response_headers

    def download(self, path: str, destination, max_bytes: int = 200 * 1024 * 1024) -> int:
        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": f"sextant-agent/{__version__}",
        }
        if self.config.agent_token:
            headers["Authorization"] = f"Bearer {self.config.agent_token}"
        request = urllib.request.Request(self.base + path, headers=headers)
        total = 0
        with self._opener.open(request, timeout=max(60, self.config.timeout)) as response:
            with open(destination, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ConnectionError("Artifact exceeds the download size limit.")
                    handle.write(chunk)
        return total
