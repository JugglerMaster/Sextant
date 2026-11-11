from __future__ import annotations

import csv
import hashlib
import hmac
import io
import ipaddress
import logging
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from . import remote_install
from . import updates
from .config import Settings
from .db import Database, DEFAULT_PROFILES, dumps, loads, parse_iso, utcnow, utcnow_iso
from .ldap_auth import authenticate_ldap
from .models import (
    AdminPasswordResetIn,
    AgentEnrollIn,
    AgentProfileIn,
    AgentProfilePatchIn,
    AgentUpdateIn,
    AgentWakeIn,
    CommandIn,
    CommandResultIn,
    EnrollmentTokenIn,
    HeartbeatIn,
    LoginIn,
    PackageRuleIn,
    PasswordChangeIn,
    ProfileTagIn,
    RemoteInstallIn,
    StorePackageIn,
    UserCreateIn,
    UserUpdateIn,
    WakeTargetIn,
    WakeTargetWakeIn,
)
from .security import (
    LoginRateLimiter,
    client_ip,
    decode_session,
    hash_password,
    hash_token,
    make_session_payload,
    new_token,
    sign_session,
    token_matches,
    validate_password,
    verify_password,
    verify_password_dummy,
)
from .store import ArtifactStore, StoreError
from .views import (
    agent_to_dict,
    bounded_json,
    command_to_dict,
    role_at_least,
    session_to_dict,
    store_package_to_dict,
    store_release_to_dict,
    telemetry_sample_to_dict,
    user_to_dict,
    validate_command_args,
    wake_target_to_dict,
)
from .wol import normalize_mac, send_magic_packet

logger = logging.getLogger("runner_control")


def _ensure_bootstrap_admin(settings: Settings, db: Database) -> None:
    if db.count_users() > 0:
        return
    password = settings.admin_password
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(18)
    else:
        problem = validate_password(password)
        if problem:
            raise RuntimeError(
                f"RC_ADMIN_PASSWORD is not acceptable: {problem} "
                "Remove it to auto-generate a strong bootstrap password instead."
            )
    assert password is not None
    db.create_user(
        username=settings.admin_user,
        role="admin",
        password_hash=hash_password(password),
        display_name=settings.admin_user,
        source="local",
    )
    if generated:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        file = settings.bootstrap_file
        file.write_text(
            "Runner Control bootstrap administrator\n"
            f"Username: {settings.admin_user}\n"
            f"Password: {password}\n\n"
            "Sign in and change this password immediately, then delete this file.\n",
            encoding="utf-8",
        )
        try:
            file.chmod(0o600)
        except OSError:
            pass
        logger.warning("Created bootstrap admin at %s. Read the password from that file.", file)
    else:
        logger.warning("Created administrator %r from RC_ADMIN_PASSWORD.", settings.admin_user)


def _public_settings(settings: Settings) -> dict[str, Any]:
    return {
        "ldap_enabled": settings.ldap is not None,
        "local_auth": True,
        "offline_after_seconds": settings.offline_after_seconds,
        "offline_command_policy": settings.offline_command_policy,
        "max_queued_commands_per_agent": settings.max_queued_commands_per_agent,
        "version": __version__,
        "public_url": settings.public_url,
    }


def _session_cookie(response: Response, settings: Settings, payload: dict[str, Any]) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=sign_session(payload, settings.secret_key),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _new_session_id() -> str:
    return "ses_" + secrets.token_hex(16)


def _session_expiry(settings: Settings) -> str:
    expires = utcnow() + timedelta(seconds=settings.session_hours * 3600)
    return expires.isoformat(timespec="seconds").replace("+00:00", "Z")


def _request_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def _validate_broadcast(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ValueError as exc:
        raise ValueError("broadcast must be an IPv4 address") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_path)
    db.initialize()
    if settings.command_retention_days:
        db.prune_commands(settings.command_retention_days)
    if settings.audit_retention_days:
        db.prune_audit(settings.audit_retention_days)
    _ensure_bootstrap_admin(settings, db)
    if settings.telemetry_history:
        db.prune_telemetry(settings.telemetry_retention_days)

    app = FastAPI(
        title="Runner Control",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.limiter = LoginRateLimiter()
    artifact_store = ArtifactStore(
        settings.store_dir,
        max_blob_bytes=settings.store_max_blob_mb * 1024 * 1024,
        max_total_bytes=settings.store_max_total_gb * 1024**3,
    )
    artifact_store.ensure()
    artifact_store.cleanup_temp()
    app.state.store = artifact_store
    app.state.update_cache = {"at": 0.0, "data": None}

    def _security_headers(response: Response, path: str) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'"
        )
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def _error(status_code: int, detail: str, path: str, **kwargs: Any) -> JSONResponse:
        return _security_headers(JSONResponse({"detail": detail}, status_code=status_code, **kwargs), path)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Use the ASGI path, not request.url.path: the old Starlette rebuilt the
        # latter from the untrusted Host header, which could skip these checks.
        path = request.scope["path"]
        if (
            path.startswith("/api/")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not path.startswith("/api/agent/")
            and path != "/api/auth/login"
        ):
            payload = decode_session(request.cookies.get(settings.cookie_name), settings.secret_key)
            if payload is None:
                return _error(401, "Authentication required.", path)
            supplied = request.headers.get("x-csrf-token")
            if not supplied or not hmac.compare_digest(supplied, payload["csrf"]):
                return _error(403, "Invalid CSRF token.", path)

        if path.startswith("/api/"):
            length = request.headers.get("content-length")
            body_limit = settings.max_body_bytes
            if path.startswith("/api/store/") and request.method in {"PUT", "POST"}:
                body_limit = max(body_limit, settings.store_max_blob_mb * 1024 * 1024)
            if length:
                try:
                    if int(length) > body_limit:
                        return _error(413, "Request body too large.", path)
                except ValueError:
                    return _error(400, "Invalid Content-Length.", path)
            elif request.method not in {"GET", "HEAD", "OPTIONS"}:
                transfer = request.headers.get("transfer-encoding", "").casefold()
                if "chunked" in transfer:
                    return _error(411, "Content-Length is required for API requests.", path)

        response = await call_next(request)
        return _security_headers(response, path)

    # Only install host validation when an explicit allowlist is configured;
    # the default public URL derives one. "*" disables the check.
    if settings.allowed_hosts and "*" not in settings.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(settings.allowed_hosts),
            www_redirect=False,
        )

    def get_db(request: Request) -> Database:
        return request.app.state.db

    def get_settings(request: Request) -> Settings:
        return request.app.state.settings

    def get_store(request: Request) -> ArtifactStore:
        return request.app.state.store

    def issue_session(
        user_row: sqlite3.Row,
        request: Request,
        database: Database,
        config: Settings,
    ) -> dict[str, Any]:
        session_id = _new_session_id()
        payload = make_session_payload(
            user_row["id"],
            user_row["session_version"],
            config.session_hours * 3600,
            session_id,
        )
        database.cleanup_sessions(config.session_cleanup_days)
        database.create_session(
            session_id=session_id,
            user_id=user_row["id"],
            expires_at=_session_expiry(config),
            ip=client_ip(request, config.trust_proxy),
            user_agent=_request_user_agent(request),
        )
        return payload

    def current_user(
        request: Request,
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        payload = decode_session(request.cookies.get(config.cookie_name), config.secret_key)
        if payload is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        session_row = database.get_session(payload["sid"])
        expires = parse_iso(session_row["expires_at"]) if session_row is not None else None
        if session_row is None or session_row["revoked_at"] or expires is None or expires <= utcnow():
            if session_row is not None and not session_row["revoked_at"]:
                database.revoke_session(payload["sid"])
            raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
        if int(session_row["user_id"]) != int(payload["uid"]):
            # The cookie is signed, but bind it to the session row as defense in
            # depth if the signing secret is ever weak or leaked.
            database.revoke_session(payload["sid"])
            raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
        row = database.get_user_by_id(payload["uid"])
        if (
            row is None
            or not row["is_active"]
            or row["session_version"] != payload["sv"]
        ):
            database.revoke_session(payload["sid"])
            raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
        database.touch_session(payload["sid"])
        user = user_to_dict(row)
        user["csrf"] = payload["csrf"]
        user["sid"] = payload["sid"]
        return user

    def require_operator(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not role_at_least(user, "operator"):
            raise HTTPException(status_code=403, detail="Operator access required.")
        return user

    def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not role_at_least(user, "admin"):
            raise HTTPException(status_code=403, detail="Administrator access required.")
        return user

    def agent_auth(
        agent_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if not token or not agent_id.startswith("agt_"):
            raise HTTPException(status_code=401, detail="Invalid agent credentials.")
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None or not token_matches(token, row["token_hash"]):
            raise HTTPException(status_code=401, detail="Invalid agent credentials.")
        return dict(row)

    def sweep_commands(database: Database, config: Settings) -> int:
        return database.sweep_stale_commands(
            dispatched_grace_seconds=config.dispatched_grace_seconds,
            queued_ttl_hours=config.queued_command_ttl_hours,
        )

    def queue_command(
        agent_id: str,
        kind: str,
        args: dict[str, Any],
        user: dict[str, Any],
        request: Request,
        database: Database,
        config: Settings,
        *,
        audit_action: str = "command.created",
        extra_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            normalized = validate_command_args(kind, args)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        with database.transaction() as conn:
            agent = conn.execute(
                "SELECT id, name, last_seen, policy, profile_id FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
            if agent is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            agent_policy = loads(agent["policy"], {})
            if not isinstance(agent_policy, dict):
                agent_policy = {}
            profile_row = conn.execute(
                "SELECT * FROM agent_profiles WHERE id = ?",
                (agent["profile_id"] or "prof_workstation",),
            ).fetchone()
            profile_actions = _policy_from_settings(_profile_settings(profile_row))
            policy_flag = {
                "shell": "allow_shell",
                "power": "allow_power",
                "service": "allow_service",
                "package": "allow_packages",
            }.get(kind)
            if policy_flag and (
                agent_policy.get(policy_flag) is False or not profile_actions.get(policy_flag, False)
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"{kind.capitalize()} commands are blocked by this agent's profile or server policy.",
                )
            last_seen = parse_iso(agent["last_seen"])
            online = bool(
                last_seen and (utcnow() - last_seen).total_seconds() <= config.offline_after_seconds
            )
            if not online and config.offline_command_policy == "reject":
                raise HTTPException(
                    status_code=409,
                    detail="Agent is offline and the server policy rejects new commands.",
                )
            queued = conn.execute(
                "SELECT COUNT(*) AS n FROM commands WHERE agent_id = ? AND status = 'queued'",
                (agent_id,),
            ).fetchone()
            if int(queued["n"]) >= config.max_queued_commands_per_agent:
                raise HTTPException(
                    status_code=409,
                    detail=f"Agent already has {queued['n']} queued commands (limit {config.max_queued_commands_per_agent}).",
                )
            command_id = "cmd_" + secrets.token_hex(12)
            now = utcnow_iso()
            conn.execute(
                """INSERT INTO commands (id, agent_id, kind, args, status, created_by, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                (command_id, agent_id, kind, dumps(normalized), user["username"], now),
            )
            detail = {
                "command_id": command_id,
                "kind": kind,
                "agent": agent["name"],
                **(extra_detail or {}),
            }
            database.audit(
                actor=user["username"],
                action=audit_action,
                target=agent_id,
                detail=detail,
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        return command_to_dict(row)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        index_file = settings.static_dir / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=500, detail="Dashboard assets are missing.")
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    if settings.static_dir.exists():
        app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "server_time": utcnow_iso()}

    @app.get("/api/auth/config")
    def auth_config(config: Settings = Depends(get_settings)) -> dict[str, Any]:
        return {
            "ldap_enabled": config.ldap is not None,
            "local_auth": True,
            "version": __version__,
            "public_url": config.public_url,
        }

    @app.post("/api/auth/login")
    def login(
        payload: LoginIn,
        request: Request,
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        ip = client_ip(request, config.trust_proxy)
        key = f"{ip}:{payload.username.casefold()}"

        def fail(reason: str, detail: str = "Invalid username or password.") -> None:
            app.state.limiter.record_failure(key)
            database.audit(
                actor=payload.username,
                action="auth.login_failed",
                target=payload.username,
                detail={"mode": payload.mode, "reason": reason},
                ip=ip,
            )
            raise HTTPException(status_code=401, detail=detail)

        retry = app.state.limiter.retry_after(key)
        if retry:
            database.audit(
                actor=payload.username,
                action="auth.login_rate_limited",
                target=payload.username,
                detail={"mode": payload.mode, "retry_after": retry},
                ip=ip,
            )
            return JSONResponse(
                {"detail": "Too many failed sign-in attempts. Try again later."},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )

        row = database.get_user_by_name(payload.username)
        if row is None or row["source"] != "local":
            # Spend the same scrypt work as a real local account so response
            # timing does not reveal whether a username exists.
            verify_password_dummy(payload.password)
        local_ok = bool(
            row is not None
            and row["source"] == "local"
            and row["is_active"]
            and verify_password(payload.password, row["password_hash"])
        )

        selected = None
        if payload.mode in {"auto", "local"} and row is not None and row["source"] == "local":
            if not row["is_active"]:
                fail("account_disabled")
            if local_ok:
                selected = row
            else:
                fail("bad_password")
        elif payload.mode == "local":
            fail("unknown_user")

        if selected is None and payload.mode in {"auto", "ad"} and config.ldap is not None:
            if row is not None and row["source"] == "local":
                fail("local_account_mismatch")
            try:
                identity = authenticate_ldap(payload.username, payload.password, config.ldap)
            except RuntimeError:
                logger.exception("LDAP authentication failed for %r", payload.username)
                database.audit(
                    actor=payload.username,
                    action="auth.ldap_unavailable",
                    target=payload.username,
                    detail={"mode": payload.mode},
                    ip=ip,
                )
                raise HTTPException(status_code=503, detail="Directory authentication is unavailable.")
            if identity is None:
                fail("bad_credentials")
            selected = database.upsert_ad_user(
                username=payload.username,
                role=identity.role,
                display_name=identity.display_name,
                email=identity.email,
            )
        elif selected is None:
            fail("unknown_user")

        if selected is None:
            fail("unknown_user")
        if not selected["is_active"]:
            fail("account_disabled")

        # Refresh last_login and use the current role from the database.
        now = utcnow_iso()
        with database.connection() as conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, selected["id"]))
        selected = database.get_user_by_id(selected["id"])
        assert selected is not None

        app.state.limiter.record_success(key)
        session = issue_session(selected, request, database, config)
        public_user = user_to_dict(selected)
        result = JSONResponse(
            {
                "user": public_user,
                "csrf": session["csrf"],
                "settings": _public_settings(config),
                "permissions": _permissions(public_user),
            }
        )
        _session_cookie(result, config, session)
        database.audit(
            actor=selected["username"],
            action="auth.login",
            target=selected["username"],
            detail={"source": selected["source"]},
            ip=ip,
        )
        return result

    @app.post("/api/auth/logout")
    def logout(
        request: Request,
        response: Response,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        database.revoke_session(user["sid"])
        _clear_session_cookie(response, config)
        response.status_code = 204
        database.audit(
            actor=user["username"],
            action="auth.logout",
            target=user["username"],
            ip=client_ip(request, config.trust_proxy),
        )
        return response

    @app.get("/api/me")
    def me(
        user: dict[str, Any] = Depends(current_user),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        public_user = {key: value for key, value in user.items() if key != "sid"}
        return {
            "user": public_user,
            "settings": _public_settings(config),
            "permissions": _permissions(user),
        }

    @app.get("/api/settings")
    def settings_view(
        config: Settings = Depends(get_settings),
        user: dict[str, Any] = Depends(current_user),
    ) -> dict[str, Any]:
        return {
            **_public_settings(config),
            "server_time": utcnow_iso(),
            "permissions": _permissions(user),
        }

    @app.post("/api/me/password")
    def change_password(
        payload: PasswordChangeIn,
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        row = database.get_user_by_id(user["id"])
        if row is None or row["source"] != "local":
            raise HTTPException(status_code=400, detail="Only local accounts can change a password here.")
        if not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        problem = validate_password(payload.new_password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if payload.current_password == payload.new_password:
            raise HTTPException(status_code=400, detail="Choose a new password.")
        database.revoke_user_sessions(row["id"])
        with database.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
                (hash_password(payload.new_password), row["id"]),
            )
        refreshed = database.get_user_by_id(row["id"])
        assert refreshed is not None
        session = issue_session(refreshed, request, database, config)
        result = JSONResponse({"user": user_to_dict(refreshed), "csrf": session["csrf"]})
        _session_cookie(result, config, session)
        database.audit(
            actor=user["username"],
            action="auth.password_changed",
            target=user["username"],
            ip=client_ip(request, config.trust_proxy),
        )
        if user["username"].casefold() == config.admin_user.casefold():
            try:
                config.bootstrap_file.unlink(missing_ok=True)
            except OSError:
                pass
        return result

    @app.get("/api/sessions")
    def my_sessions(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        rows = database.list_sessions(user["id"])
        return {"sessions": [session_to_dict(row, user["sid"]) for row in rows]}

    @app.get("/api/users/{user_id}/sessions")
    def user_sessions(
        user_id: int,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        target = database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        rows = database.list_sessions(user_id)
        return {
            "user": user_to_dict(target),
            "sessions": [session_to_dict(row, user["sid"]) for row in rows],
        }

    @app.delete("/api/sessions/{session_id}")
    def revoke_one_session(
        session_id: str,
        request: Request,
        response: Response,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        target = database.get_session(session_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if target["user_id"] != user["id"] and not role_at_least(user, "admin"):
            raise HTTPException(status_code=403, detail="Administrator access required.")
        database.revoke_session(session_id)
        database.audit(
            actor=user["username"],
            action="session.revoked",
            target=session_id,
            detail={"user_id": target["user_id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        if session_id == user["sid"]:
            _clear_session_cookie(response, config)
            response.status_code = 204
            return response
        response.status_code = 204
        return response

    @app.post("/api/users/{user_id}/revoke-sessions")
    def revoke_user_sessions(
        user_id: int,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        target = database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        revoked = database.revoke_user_sessions(user_id)
        with database.connection() as conn:
            conn.execute(
                "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                (user_id,),
            )
        database.audit(
            actor=user["username"],
            action="sessions.revoked",
            target=target["username"],
            detail={"count": revoked, "self": user_id == user["id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        result: dict[str, Any] = {"revoked": revoked}
        if user_id == user["id"]:
            refreshed = database.get_user_by_id(user_id)
            assert refreshed is not None
            session = issue_session(refreshed, request, database, config)
            result["csrf"] = session["csrf"]
            result["user"] = user_to_dict(refreshed)
            response = JSONResponse(result)
            _session_cookie(response, config, session)
            return response
        return JSONResponse(result)

    @app.post("/api/users/{user_id}/reset-password")
    def admin_reset_password(
        user_id: int,
        payload: AdminPasswordResetIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> JSONResponse:
        target = database.get_user_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found.")
        if target["source"] != "local":
            raise HTTPException(status_code=400, detail="Only local accounts can have a local password reset.")
        problem = validate_password(payload.new_password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        database.revoke_user_sessions(user_id)
        with database.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
                (hash_password(payload.new_password), user_id),
            )
        database.audit(
            actor=user["username"],
            action="user.password_reset",
            target=target["username"],
            detail={"self": user_id == user["id"]},
            ip=client_ip(request, config.trust_proxy),
        )
        if target["username"].casefold() == config.admin_user.casefold():
            try:
                config.bootstrap_file.unlink(missing_ok=True)
            except OSError:
                pass
        result: dict[str, Any] = {"user_id": user_id, "username": target["username"]}
        if user_id == user["id"]:
            refreshed = database.get_user_by_id(user_id)
            assert refreshed is not None
            session = issue_session(refreshed, request, database, config)
            result["csrf"] = session["csrf"]
            result["user"] = user_to_dict(refreshed)
            response = JSONResponse(result)
            _session_cookie(response, config, session)
            return response
        return JSONResponse(result)

    @app.get("/api/dashboard")
    def dashboard(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        sweep_commands(database, config)
        now = datetime.now(timezone.utc)
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name COLLATE NOCASE").fetchall()
            queued = conn.execute("SELECT COUNT(*) AS n FROM commands WHERE status IN ('queued', 'dispatched', 'running')").fetchone()
        agents = [agent_to_dict(row, config, now) for row in rows]
        agents.sort(key=lambda item: (not item["online"], item["name"].casefold()))
        online = sum(1 for item in agents if item["online"])
        return {
            "server_time": utcnow_iso(),
            "stats": {
                "total": len(agents),
                "online": online,
                "offline": len(agents) - online,
                "alerts": sum(item["alert_count"] for item in agents),
                "active_commands": int(queued["n"]),
            },
            "agents": agents,
        }

    @app.get("/api/agents/{agent_id}")
    def agent_detail(
        agent_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            commands = conn.execute(
                "SELECT * FROM commands WHERE agent_id = ? ORDER BY created_at DESC LIMIT 50",
                (agent_id,),
            ).fetchall()
        return {
            "agent": agent_to_dict(row, config),
            "commands": [command_to_dict(command, full_output=False) for command in commands],
        }

    @app.get("/api/agents/{agent_id}/history")
    def agent_history(
        agent_id: str,
        hours: int = Query(default=24, description="Lookback window in hours (1-720)"),
        points: int = Query(default=200, description="Maximum number of time buckets (10-1000)"),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        if not 1 <= hours <= 720:
            raise HTTPException(status_code=400, detail="hours must be between 1 and 720.")
        if not 10 <= points <= 1000:
            raise HTTPException(status_code=400, detail="points must be between 10 and 1000.")
        with database.connection() as conn:
            exists = conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        rows = database.telemetry_history(agent_id, hours, points)
        return {
            "agent_id": agent_id,
            "hours": hours,
            "points": [telemetry_sample_to_dict(row) for row in rows],
        }

    @app.patch("/api/agents/{agent_id}")
    def update_agent(
        agent_id: str,
        payload: AgentUpdateIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if all(
            value is None
            for value in (
                payload.name,
                payload.tags,
                payload.notes,
                payload.watch_services,
                payload.profile,
                payload.allow_shell,
                payload.allow_power,
                payload.allow_service,
                payload.allow_packages,
            )
        ):
            raise HTTPException(status_code=400, detail="No agent changes supplied.")
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            tags = loads(row["tags"], [])
            watch_services = loads(row["watch_services"], [])
            policy = loads(row["policy"], {})
            if not isinstance(tags, list):
                tags = []
            if not isinstance(watch_services, list):
                watch_services = []
            if not isinstance(policy, dict):
                policy = {}
            if payload.name is not None:
                row_name = payload.name.strip()
            else:
                row_name = row["name"]
            if payload.tags is not None:
                tags = [str(tag).strip()[:64] for tag in payload.tags if str(tag).strip()][:32]
            if payload.watch_services is not None:
                watch_services = [
                    str(service).strip()[:255]
                    for service in payload.watch_services
                    if str(service).strip()
                ][:64]
            profile_id = row["profile_id"]
            if payload.profile is not None:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE id = ? OR slug = ? COLLATE NOCASE",
                    (payload.profile, payload.profile),
                ).fetchone()
                if profile_row is None:
                    raise HTTPException(status_code=400, detail="Unknown agent profile.")
                profile_id = profile_row["id"]
            elif payload.tags is not None:
                tag_profile_id = database.profile_for_tags(tags)
                if tag_profile_id:
                    profile_id = tag_profile_id
            for flag in ("allow_shell", "allow_power", "allow_service", "allow_packages"):
                value = getattr(payload, flag)
                if value is not None:
                    policy[flag] = bool(value)
            conn.execute(
                """UPDATE agents SET name = ?, tags = ?, notes = ?, watch_services = ?,
                   profile_id = ?, policy = ? WHERE id = ?""",
                (
                    row_name,
                    dumps(tags),
                    payload.notes if payload.notes is not None else row["notes"],
                    dumps(watch_services),
                    profile_id,
                    dumps(
                        {
                            "allow_shell": bool(policy.get("allow_shell", True)),
                            "allow_power": bool(policy.get("allow_power", True)),
                            "allow_service": bool(policy.get("allow_service", True)),
                            "allow_packages": bool(policy.get("allow_packages", True)),
                        }
                    ),
                    agent_id,
                ),
            )
            database.audit(
                actor=user["username"],
                action="agent.updated",
                target=agent_id,
                detail={
                    "name": row_name,
                    "tags": tags,
                    "watch_services": watch_services,
                    "profile_id": profile_id,
                    "allow_shell": bool(policy.get("allow_shell", True)),
                    "allow_power": bool(policy.get("allow_power", True)),
                    "allow_service": bool(policy.get("allow_service", True)),
                    "allow_packages": bool(policy.get("allow_packages", True)),
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return agent_to_dict(updated, config)

    @app.post("/api/agents/{agent_id}/commands")
    def create_command(
        agent_id: str,
        payload: CommandIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        return queue_command(agent_id, payload.kind, payload.args, user, request, database, config)

    @app.post("/api/commands/{command_id}/cancel")
    def cancel_command(
        command_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found.")
            if row["status"] != "queued":
                raise HTTPException(
                    status_code=409,
                    detail="Only commands still queued for an agent can be cancelled.",
                )
            conn.execute(
                """UPDATE commands SET status = 'cancelled', completed_at = ?, error = ?
                   WHERE id = ? AND status = 'queued'""",
                (utcnow_iso(), "Cancelled by operator before dispatch.", command_id),
            )
            database.audit(
                actor=user["username"],
                action="command.cancelled",
                target=row["agent_id"],
                detail={"command_id": command_id},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        return command_to_dict(updated)

    @app.post("/api/commands/{command_id}/retry")
    def retry_command(
        command_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            original = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if original is None:
            raise HTTPException(status_code=404, detail="Command not found.")
        if original["status"] not in {"failed", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail="Only failed or cancelled commands can be retried.",
            )
        return queue_command(
            original["agent_id"],
            original["kind"],
            loads(original["args"], {}),
            user,
            request,
            database,
            config,
            audit_action="command.retried",
            extra_detail={"original_command_id": command_id},
        )

    @app.get("/api/agents/{agent_id}/commands")
    def list_commands(
        agent_id: str,
        limit: int = 50,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        limit = max(1, min(int(limit), 200))
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if agent is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            rows = conn.execute(
                "SELECT * FROM commands WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        return {"commands": [command_to_dict(row, full_output=False) for row in rows]}

    @app.get("/api/commands/{command_id}")
    def get_command(
        command_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Command not found.")
        return {"command": command_to_dict(row)}

    @app.post("/api/agents/{agent_id}/wake")
    def wake_agent(
        agent_id: str,
        payload: AgentWakeIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        if not row["mac"]:
            raise HTTPException(status_code=400, detail="Agent has not reported a MAC address.")
        try:
            mac = normalize_mac(row["mac"])
            broadcast = _validate_broadcast(payload.broadcast)
            packets = send_magic_packet(mac, broadcast, payload.port, payload.packets)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError as exc:
            logger.warning("WoL send failed: %s", exc)
            raise HTTPException(status_code=502, detail="Could not send the wake packet.")
        database.audit(
            actor=user["username"],
            action="wol.sent",
            target=agent_id,
            detail={"mac": mac, "broadcast": broadcast, "port": payload.port, "packets": packets},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"sent": packets, "mac": mac, "broadcast": broadcast, "port": payload.port}

    @app.get("/api/wake-targets")
    def list_wake_targets(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM wake_targets ORDER BY name COLLATE NOCASE").fetchall()
        return {"targets": [wake_target_to_dict(row) for row in rows]}

    @app.post("/api/wake-targets")
    def create_wake_target(
        payload: WakeTargetIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        try:
            mac = normalize_mac(payload.mac)
            broadcast = _validate_broadcast(payload.broadcast)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        target_id = "wol_" + secrets.token_hex(8)
        with database.connection() as conn:
            conn.execute(
                """INSERT INTO wake_targets (id, name, mac, ip, broadcast, port, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (target_id, payload.name, mac, payload.ip, broadcast, payload.port, user["username"], utcnow_iso()),
            )
            database.audit(
                actor=user["username"],
                action="wol.target_created",
                target=target_id,
                detail={"name": payload.name, "mac": mac},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            row = conn.execute("SELECT * FROM wake_targets WHERE id = ?", (target_id,)).fetchone()
        return wake_target_to_dict(row)

    @app.delete("/api/wake-targets/{target_id}")
    def delete_wake_target(
        target_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM wake_targets WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Wake target not found.")
            conn.execute("DELETE FROM wake_targets WHERE id = ?", (target_id,))
            database.audit(
                actor=user["username"],
                action="wol.target_deleted",
                target=target_id,
                detail={"name": row["name"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return Response(status_code=204)

    @app.post("/api/wake-targets/{target_id}/wake")
    def wake_target(
        target_id: str,
        payload: WakeTargetWakeIn,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM wake_targets WHERE id = ?", (target_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Wake target not found.")
            try:
                broadcast = _validate_broadcast(payload.broadcast or row["broadcast"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            port = payload.port or int(row["port"])
            try:
                packets = send_magic_packet(row["mac"], broadcast, port, payload.packets)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except OSError:
                raise HTTPException(status_code=502, detail="Could not send the wake packet.")
            now = utcnow_iso()
            conn.execute("UPDATE wake_targets SET last_woken_at = ? WHERE id = ?", (now, target_id))
            database.audit(
                actor=user["username"],
                action="wol.sent",
                target=target_id,
                detail={"mac": row["mac"], "broadcast": broadcast, "port": port},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return {"sent": packets, "mac": row["mac"], "broadcast": broadcast, "port": port}

    @app.post("/api/enrollment-tokens")
    def create_enrollment_token(
        payload: EnrollmentTokenIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        token = new_token("rc_enroll_")
        token_id = "enr_" + secrets.token_hex(8)
        now = utcnow()
        minutes = payload.expires_minutes if payload.expires_minutes is not None else config.enrollment_ttl_minutes
        expires = now + timedelta(minutes=minutes)
        with database.connection() as conn:
            conn.execute(
                """INSERT INTO enrollment_tokens
                   (id, token_hash, label, created_by, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    token_id,
                    hash_token(token),
                    payload.label,
                    user["username"],
                    now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
                ),
            )
            database.audit(
                actor=user["username"],
                action="enrollment.created",
                target=token_id,
                detail={"label": payload.label, "expires_at": expires.isoformat()},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        base = config.public_url or str(request.base_url).rstrip("/")
        return {
            "id": token_id,
            "token": token,
            "label": payload.label,
            "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "install_command": f"runner-agent enroll --server {base} --token {token}",
        }

    @app.get("/api/enrollment-tokens")
    def list_enrollment_tokens(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        now = utcnow()
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM enrollment_tokens ORDER BY created_at DESC LIMIT 100").fetchall()
        tokens = []
        for row in rows:
            expires = parse_iso(row["expires_at"])
            tokens.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                    "used_at": row["used_at"],
                    "used_by_agent": row["used_by_agent"],
                    "state": "used" if row["used_at"] else ("expired" if expires and expires < now else "active"),
                }
            )
        return {"tokens": tokens}

    @app.delete("/api/enrollment-tokens/{token_id}")
    def delete_enrollment_token(
        token_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.connection() as conn:
            row = conn.execute("SELECT * FROM enrollment_tokens WHERE id = ?", (token_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Enrollment token not found.")
            if row["used_at"]:
                raise HTTPException(status_code=409, detail="Enrollment token has already been used.")
            conn.execute("DELETE FROM enrollment_tokens WHERE id = ?", (token_id,))
            database.audit(
                actor=user["username"],
                action="enrollment.revoked",
                target=token_id,
                detail={"label": row["label"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return Response(status_code=204)

    @app.get("/api/users")
    def list_users(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return {
            "users": [
                user_to_dict(row, session_count=database.count_sessions(row["id"]))
                for row in rows
            ]
        }

    @app.post("/api/users")
    def create_user(
        payload: UserCreateIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        problem = validate_password(payload.password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if database.get_user_by_name(payload.username) is not None:
            raise HTTPException(status_code=409, detail="That username already exists.")
        try:
            row = database.create_user(
                username=payload.username,
                role=payload.role,
                password_hash=hash_password(payload.password),
                display_name=payload.display_name,
                email=payload.email,
                source="local",
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="That username already exists.")
        database.audit(
            actor=user["username"],
            action="user.created",
            target=payload.username,
            detail={"role": payload.role},
            ip=client_ip(request, config.trust_proxy),
        )
        return user_to_dict(row)

    @app.patch("/api/users/{user_id}")
    def update_user(
        user_id: int,
        payload: UserUpdateIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if payload.role is None and payload.active is None:
            raise HTTPException(status_code=400, detail="Provide role and/or active.")
        if user_id == user["id"] and payload.active is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="User not found.")
            new_role = payload.role if payload.role is not None else row["role"]
            new_active = payload.active if payload.active is not None else bool(row["is_active"])
            if row["role"] == "admin" and row["is_active"] and (new_role != "admin" or not new_active):
                admins = conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()["n"]
                if int(admins) <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot demote or deactivate the last active administrator.",
                    )
            conn.execute(
                "UPDATE users SET role = ?, is_active = ? WHERE id = ?",
                (new_role, 1 if new_active else 0, user_id),
            )
            if row["is_active"] and not new_active:
                conn.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                    (utcnow_iso(), user_id),
                )
                conn.execute(
                    "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                    (user_id,),
                )
            database.audit(
                actor=user["username"],
                action="user.updated",
                target=row["username"],
                detail={
                    "from_role": row["role"],
                    "to_role": new_role,
                    "from_active": bool(row["is_active"]),
                    "to_active": new_active,
                },
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return user_to_dict(updated)

    @app.get("/api/audit")
    def audit_log(
        limit: int = 100,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        limit = max(1, min(int(limit), 500))
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {
            "events": [
                {
                    "id": row["id"],
                    "at": row["at"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "target": row["target"],
                    "detail": loads(row["detail"], {}),
                    "ip": row["ip"],
                }
                for row in rows
            ]
        }

    @app.post("/api/agents/{agent_id}/rotate-token")
    def rotate_agent_token(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        token = new_token("rc_agent_")
        with database.connection() as conn:
            row = conn.execute("SELECT id, name FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            conn.execute("UPDATE agents SET token_hash = ? WHERE id = ?", (hash_token(token), agent_id))
            database.audit(
                actor=user["username"],
                action="agent.token_rotated",
                target=agent_id,
                detail={"agent": row["name"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return {"agent_id": agent_id, "agent_token": token}

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        with database.connection() as conn:
            row = conn.execute("SELECT id, name FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent not found.")
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            database.audit(
                actor=user["username"],
                action="agent.deleted",
                target=agent_id,
                detail={"agent": row["name"]},
                ip=client_ip(request, config.trust_proxy),
                conn=conn,
            )
        return Response(status_code=204)

    # ---- Agent-facing API -------------------------------------------------

    @app.post("/api/agent/enroll")
    def enroll_agent(
        payload: AgentEnrollIn,
        request: Request,
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        now = utcnow()
        token_hash = hash_token(payload.enrollment_token)
        with database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM enrollment_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            expires = parse_iso(row["expires_at"]) if row else None
            if row is None or row["used_at"] or expires is None or expires < now:
                raise HTTPException(status_code=401, detail="Invalid or expired enrollment token.")
            machine_id = payload.machine_id.strip()[:128]
            tags = [str(tag)[:64] for tag in payload.tags[:32]]
            watch_services = [str(service)[:255] for service in payload.watch_services[:64]]
            existing = None
            if machine_id:
                existing = conn.execute(
                    "SELECT * FROM agents WHERE machine_id = ? AND machine_id != '' ORDER BY enrolled_at LIMIT 1",
                    (machine_id,),
                ).fetchone()
            reenrolled = existing is not None
            profile_row = None
            if payload.profile:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE slug = ? COLLATE NOCASE", (payload.profile,)
                ).fetchone()
                if profile_row is None:
                    raise HTTPException(status_code=400, detail="Unknown agent profile.")
            if profile_row is None and existing is not None:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE id = ?",
                    (existing["profile_id"] or "prof_workstation",),
                ).fetchone()
            if profile_row is None:
                profile_row = conn.execute(
                    "SELECT * FROM agent_profiles WHERE id = 'prof_workstation'"
                ).fetchone()
            profile_id = profile_row["id"] if profile_row is not None else "prof_workstation"
            tag_normalized = {str(tag).casefold() for tag in tags}
            for assignment in conn.execute(
                "SELECT tag, profile_id FROM profile_tag_assignments ORDER BY tag"
            ).fetchall():
                if str(assignment["tag"]).casefold() in tag_normalized:
                    profile_row = conn.execute(
                        "SELECT * FROM agent_profiles WHERE id = ?", (assignment["profile_id"],)
                    ).fetchone()
                    if profile_row is not None:
                        profile_id = profile_row["id"]
                    break
            profile_settings = loads(profile_row["settings"], {}) if profile_row is not None else {}
            default_policy = dumps(
                _policy_from_settings(profile_settings)
                if profile_settings
                else {
                    "allow_shell": True,
                    "allow_power": True,
                    "allow_service": True,
                    "allow_packages": False,
                }
            )
            agent_id = existing["id"] if existing else "agt_" + secrets.token_hex(12)
            agent_token = new_token("rc_agent_")
            update = conn.execute(
                "UPDATE enrollment_tokens SET used_at = ?, used_by_agent = ? WHERE id = ? AND used_at IS NULL",
                (utcnow_iso(), agent_id, row["id"]),
            )
            if update.rowcount != 1:
                raise HTTPException(status_code=401, detail="Invalid or expired enrollment token.")
            if existing:
                conn.execute(
                    """UPDATE agents SET
                       name = ?, hostname = ?, machine_id = ?, os = ?, os_version = ?, arch = ?,
                       ip = ?, mac = ?, token_hash = ?, agent_version = ?, profile_id = ?, policy = ?, enrolled_at = ?, last_seen = NULL
                       WHERE id = ?""",
                    (
                        (payload.name or payload.hostname)[:255],
                        payload.hostname,
                        machine_id,
                        payload.os,
                        payload.os_version,
                        payload.arch,
                        payload.ip,
                        payload.mac,
                        hash_token(agent_token),
                        payload.agent_version,
                        profile_id,
                        default_policy,
                        utcnow_iso(),
                        agent_id,
                    ),
                )
                database.audit(
                    actor="enrollment",
                    action="agent.reenrolled",
                    target=agent_id,
                    detail={"hostname": payload.hostname, "label": row["label"]},
                    ip=client_ip(request, config.trust_proxy),
                    conn=conn,
                )
            else:
                conn.execute(
                    """INSERT INTO agents
                       (id, name, hostname, machine_id, os, os_version, arch, ip, mac, token_hash,
                        agent_version, tags, notes, watch_services, policy, profile_id, enrolled_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        agent_id,
                        (payload.name or payload.hostname)[:255],
                        payload.hostname,
                        machine_id,
                        payload.os,
                        payload.os_version,
                        payload.arch,
                        payload.ip,
                        payload.mac,
                        hash_token(agent_token),
                        payload.agent_version,
                        dumps(tags),
                        payload.notes,
                        dumps(watch_services),
                        default_policy,
                        profile_id,
                        utcnow_iso(),
                    ),
                )
                database.audit(
                    actor="enrollment",
                    action="agent.enrolled",
                    target=agent_id,
                    detail={"hostname": payload.hostname, "label": row["label"]},
                    ip=client_ip(request, config.trust_proxy),
                    conn=conn,
                )
        return {
            "agent_id": agent_id,
            "agent_token": agent_token,
            "reenrolled": reenrolled,
            "server_time": utcnow_iso(),
            "heartbeat_interval": int(profile_settings.get("heartbeat_seconds", 30)),
            "profile": profile_row["slug"] if profile_row is not None else "workstation",
            "version": __version__,
        }

    @app.post("/api/agent/{agent_id}/heartbeat")
    def heartbeat(
        agent_id: str,
        payload: HeartbeatIn,
        request: Request,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del request
        sweep_commands(database, config)
        now = utcnow()
        disks = []
        for disk in payload.disks:
            percent = float(disk.percent)
            if disk.total > 0:
                percent = min(100.0, max(0.0, disk.used / disk.total * 100.0))
            disks.append(
                {
                    "mount": disk.mount,
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": round(percent, 1),
                    "fs": disk.fs,
                }
            )
        gpus = [gpu.model_dump() for gpu in payload.gpus]
        services = [
            {
                "name": service.name,
                "display_name": service.display_name,
                "status": service.status,
                "startup": service.startup,
                "watched": service.watched,
            }
            for service in payload.services
        ]
        startup = [entry.model_dump() for entry in payload.startup]
        mem_pct = payload.mem_pct
        if payload.mem_total > 0:
            mem_pct = min(100.0, max(0.0, payload.mem_used / payload.mem_total * 100.0))
        disk_max_pct = max((float(disk["percent"]) for disk in disks), default=None)
        gpu_max_util = max((float(gpu["util_pct"]) for gpu in gpus), default=None)
        gpu_max_temp = max((float(gpu["temp_c"]) for gpu in gpus), default=None)
        service_down = sum(
            1
            for service in services
            if service["watched"] and str(service["status"]).lower() not in {"running", "active"}
        )
        stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")

        with database.transaction() as conn:
            conn.execute(
                """UPDATE agents SET
                   hostname = ?, machine_id = ?, os = ?, os_version = ?, arch = ?, ip = ?, mac = ?,
                   agent_version = ?, last_seen = ?, service_inventory_at = ?, cpu_pct = ?, mem_pct = ?, mem_total = ?, mem_used = ?,
                   uptime_seconds = ?, disks = ?, gpus = ?, services = ?, startup = ?, extra = ?
                   WHERE id = ?""",
                (
                    (payload.hostname or agent["hostname"])[:255],
                    (agent["machine_id"] or payload.machine_id)[:128],
                    (payload.os or agent["os"])[:64],
                    (payload.os_version or agent["os_version"])[:255],
                    (payload.arch or agent["arch"])[:64],
                    (payload.ip or agent["ip"])[:128],
                    (payload.mac or agent["mac"])[:64],
                    (payload.agent_version or agent["agent_version"])[:64],
                    stamp,
                    payload.service_inventory_collected_at or agent["service_inventory_at"],
                    round(float(payload.cpu_pct), 1),
                    round(float(mem_pct), 1),
                    payload.mem_total,
                    payload.mem_used,
                    payload.uptime_seconds,
                    dumps(disks),
                    dumps(gpus),
                    dumps(services),
                    dumps(startup),
                    bounded_json(payload.extra, 50_000),
                    agent_id,
                ),
            )
            if config.telemetry_history:
                database.insert_telemetry_sample(
                    conn,
                    agent_id,
                    stamp,
                    cpu_pct=round(float(payload.cpu_pct), 1),
                    mem_pct=round(float(mem_pct), 1),
                    mem_used=payload.mem_used,
                    mem_total=payload.mem_total,
                    uptime_seconds=payload.uptime_seconds,
                    disk_max_pct=disk_max_pct,
                    gpu_max_util=gpu_max_util,
                    gpu_max_temp=gpu_max_temp,
                    service_down=service_down,
                )
            inventory_state = database.get_package_inventory_state(agent_id)
            current_revision = int(inventory_state["revision"]) if inventory_state is not None else 0
            received_hash = payload.package_inventory_hash
            has_changes = bool(payload.packages) or bool(payload.package_inventory_deletes)
            request_inventory_full = ""
            new_revision = current_revision
            mode = payload.package_inventory_mode
            if mode == "delta":
                if inventory_state is None or payload.package_inventory_base_revision != current_revision:
                    request_inventory_full = "full"
                elif has_changes:
                    database.apply_package_inventory_delta(
                        conn,
                        agent_id,
                        [item.model_dump() for item in payload.packages],
                        payload.package_inventory_deletes,
                    )
                    new_revision = current_revision + 1
                    database.set_package_inventory_state(
                        conn,
                        agent_id,
                        hash_value=received_hash,
                        revision=new_revision,
                        full=False,
                    )
            else:
                if payload.package_inventory_full or (
                    has_changes and (inventory_state is None or inventory_state["hash"] != received_hash)
                ):
                    database.replace_package_inventory(
                        conn,
                        agent_id,
                        [item.model_dump() for item in payload.packages],
                        [item.model_dump() for item in payload.package_managers],
                    )
                    new_revision = current_revision + 1
                    database.set_package_inventory_state(
                        conn,
                        agent_id,
                        hash_value=received_hash,
                        revision=new_revision,
                        full=True,
                    )
                elif received_hash and inventory_state is not None and inventory_state["hash"] != received_hash and not has_changes:
                    request_inventory_full = "full"
            rows = conn.execute(
                """SELECT * FROM commands
                   WHERE agent_id = ? AND status = 'queued'
                   ORDER BY created_at ASC LIMIT 1""",
                (agent_id,),
            ).fetchall()
            dispatch: list[dict[str, Any]] = []
            stamp = utcnow_iso()
            for row in rows:
                updated = conn.execute(
                    "UPDATE commands SET status = 'dispatched', dispatched_at = ? WHERE id = ? AND status = 'queued'",
                    (stamp, row["id"]),
                )
                if updated.rowcount == 1:
                    row = conn.execute("SELECT * FROM commands WHERE id = ?", (row["id"],)).fetchone()
                    dispatch.append(
                        {
                            "id": row["id"],
                            "kind": row["kind"],
                            "args": loads(row["args"], {}),
                            "created_at": row["created_at"],
                        }
                    )
        if secrets.randbelow(100) == 0:
            if config.telemetry_history:
                database.prune_telemetry(config.telemetry_retention_days)
            database.prune_commands(config.command_retention_days)
            database.prune_audit(config.audit_retention_days)
        policy = loads(agent["policy"], {})
        if not isinstance(policy, dict):
            policy = {}
        profile_row = database.get_profile_by_id(agent["profile_id"]) or database.get_profile_by_slug("workstation")
        profile_settings = _profile_settings(profile_row)
        profile_actions = _policy_from_settings(profile_settings)
        watch_services = loads(agent["watch_services"], [])
        if not isinstance(watch_services, list):
            watch_services = []
        policy = {
            "allow_shell": bool(policy.get("allow_shell", True)) and profile_actions["allow_shell"],
            "allow_power": bool(policy.get("allow_power", True)) and profile_actions["allow_power"],
            "allow_service": bool(policy.get("allow_service", True)) and profile_actions["allow_service"],
            "allow_packages": bool(policy.get("allow_packages", True)) and profile_actions["allow_packages"],
            "watch_services": watch_services,
        }
        current_version = payload.agent_version or agent["agent_version"] or "0"
        agent_update_info: dict[str, Any] = {"available": False, "current_version": current_version}
        release = _latest_agent_release(database)
        if release is not None:
            agent_update_info["latest_version"] = release["version"]
            if updates.parse_version(release["version"]) > updates.parse_version(current_version):
                agent_update_info = {
                    "available": True,
                    "current_version": current_version,
                    "version": release["version"],
                    "release_id": release["id"],
                    "sha256": release["sha256"],
                    "size": release["size"],
                    "filename": release["filename"],
                    "channel": release["channel"],
                    "notes_url": release["notes_url"],
                    "download_url": f"/api/agent/{agent_id}/store/releases/{release['id']}/download",
                }
        return {
            "server_time": utcnow_iso(),
            "offline_after_seconds": config.offline_after_seconds,
            "policy": policy,
            "profile": _profile_payload(profile_row),
            "request_package_inventory": request_inventory_full,
            "package_inventory_revision": new_revision,
            "commands": dispatch,
            "agent_update": agent_update_info,
        }

    @app.post("/api/agent/{agent_id}/commands/{command_id}/result")
    def agent_command_result(
        agent_id: str,
        command_id: str,
        payload: CommandResultIn,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del agent
        limit = config.command_output_limit
        stdout = payload.stdout[:limit]
        stderr = payload.stderr[:limit]
        final_status = payload.status
        if payload.exit_code not in (None, 0) and final_status == "succeeded":
            final_status = "failed"
        with database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM commands WHERE id = ? AND agent_id = ?",
                (command_id, agent_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Command not found.")
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return {"command": command_to_dict(row), "idempotent": True}
            if row["status"] == "queued":
                raise HTTPException(status_code=409, detail="Command has not been dispatched yet.")
            updated = conn.execute(
                """UPDATE commands SET status = ?, completed_at = ?, exit_code = ?, stdout = ?, stderr = ?, error = ?
                   WHERE id = ? AND agent_id = ? AND status IN ('dispatched', 'running')""",
                (
                    final_status,
                    utcnow_iso(),
                    payload.exit_code,
                    stdout,
                    stderr,
                    payload.error[:4000],
                    command_id,
                    agent_id,
                ),
            )
            if updated.rowcount != 1:
                latest = conn.execute(
                    "SELECT * FROM commands WHERE id = ? AND agent_id = ?",
                    (command_id, agent_id),
                ).fetchone()
                if latest is not None:
                    return {"command": command_to_dict(latest), "idempotent": True}
                raise HTTPException(status_code=404, detail="Command not found.")
            conn.execute("UPDATE agents SET last_seen = ? WHERE id = ?", (utcnow_iso(), agent_id))
            updated = conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        return {"command": command_to_dict(updated)}

    # ---- Package store (Tier 0) ---------------------------------------

    STORE_ARTIFACT_TYPES = {"agent-wheel", "deb", "rpm", "msi", "msix", "zip", "exe"}
    VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

    @app.get("/api/store/packages")
    def list_store_packages(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return {"packages": [store_package_to_dict(row) for row in database.list_store_packages()]}

    @app.get("/api/store/packages/{slug}")
    def get_store_package(
        slug: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        package = database.get_store_package(slug)
        if package is None:
            raise HTTPException(status_code=404, detail="Store package not found.")
        releases = [store_release_to_dict(row) for row in database.list_store_releases(package["id"])]
        return {"package": store_package_to_dict(package), "releases": releases}

    @app.post("/api/store/packages")
    def create_store_package(
        payload: StorePackageIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not config.store_enabled:
            raise HTTPException(status_code=404, detail="The package store is disabled.")
        try:
            row = database.create_store_package(
                slug=payload.slug,
                name=payload.name,
                vendor=payload.vendor,
                description=payload.description,
                kind=payload.kind,
                platforms=payload.platforms,
                actor=user["username"],
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="A package with that slug already exists.")
        database.audit(
            actor=user["username"],
            action="store.package_created",
            target=row["slug"],
            detail={"name": payload.name, "kind": payload.kind},
            ip=client_ip(request, config.trust_proxy),
        )
        return store_package_to_dict(row)

    @app.put("/api/store/packages/{slug}/releases/{version}")
    async def upload_store_release(
        slug: str,
        version: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        store: ArtifactStore = Depends(get_store),
    ) -> dict[str, Any]:
        if not config.store_enabled:
            raise HTTPException(status_code=404, detail="The package store is disabled.")
        if not VERSION_RE.match(version):
            raise HTTPException(status_code=400, detail="Invalid version.")
        package = database.get_store_package(slug)
        if package is None:
            raise HTTPException(status_code=404, detail="Store package not found.")
        artifact_type = request.headers.get("x-artifact-type", "").strip().lower()
        if artifact_type not in STORE_ARTIFACT_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported artifact type.")
        filename = Path(request.headers.get("x-filename", f"{slug}-{version}.bin")).name[:255]
        expected_sha = request.headers.get("x-sha256", "").strip().lower()
        channel = request.headers.get("x-channel", "stable").strip().lower() or "stable"
        if channel not in {"stable", "beta"}:
            raise HTTPException(status_code=400, detail="Channel must be stable or beta.")
        min_version = request.headers.get("x-min-version", "").strip()[:64]
        notes_url = request.headers.get("x-notes-url", "").strip()[:500]
        mandatory = request.headers.get("x-mandatory", "").strip().lower() in {"1", "true", "yes"}

        temp_path = store.new_temp_path()
        digest = hashlib.sha256()
        size = 0
        try:
            with temp_path.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > store.max_blob_bytes:
                        raise HTTPException(status_code=413, detail="Artifact exceeds the per-file limit.")
                    digest.update(chunk)
                    handle.write(chunk)
            sha256 = digest.hexdigest()
            if expected_sha and expected_sha != sha256:
                raise HTTPException(status_code=400, detail="SHA-256 does not match the uploaded bytes.")
            try:
                store.commit(temp_path, sha256, size)
            except StoreError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            manifest = {
                "schema": 1,
                "slug": slug,
                "version": version,
                "artifact_type": artifact_type,
                "filename": filename,
                "sha256": sha256,
                "size": size,
                "channel": channel,
                "min_version": min_version,
                "mandatory": mandatory,
                "notes_url": notes_url,
                "signature": None,
                "key_id": None,
            }
            try:
                row = database.create_store_release(
                    package_id=package["id"],
                    version=version,
                    artifact_type=artifact_type,
                    filename=filename,
                    size=size,
                    sha256=sha256,
                    manifest=manifest,
                    actor=user["username"],
                    channel=channel,
                    min_version=min_version,
                    mandatory=mandatory,
                    notes_url=notes_url,
                )
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=409, detail="That version already exists.")
        finally:
            temp_path.unlink(missing_ok=True)

        database.store_event(
            "published",
            package_id=package["id"],
            release_id=row["id"],
            detail={"version": version, "sha256": sha256, "size": size},
            actor=user["username"],
        )
        database.audit(
            actor=user["username"],
            action="store.release_published",
            target=row["id"],
            detail={"slug": slug, "version": version, "sha256": sha256},
            ip=client_ip(request, config.trust_proxy),
        )
        return store_release_to_dict(row)

    @app.post("/api/store/releases/{release_id}/yank")
    def yank_store_release(
        release_id: str,
        request: Request,
        reason: str = Query(default="", max_length=500),
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        release = database.get_store_release(release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="Release not found.")
        database.yank_store_release(release_id, reason)
        database.store_event("yanked", package_id=release["package_id"], release_id=release_id, detail={"reason": reason}, actor=user["username"])
        database.audit(
            actor=user["username"],
            action="store.release_yanked",
            target=release_id,
            detail={"reason": reason},
            ip=client_ip(request, config.trust_proxy),
        )
        updated = database.get_store_release(release_id)
        return store_release_to_dict(updated)

    @app.get("/api/agent/{agent_id}/store/releases/{release_id}/download")
    def download_store_release(
        agent_id: str,
        release_id: str,
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
        store: ArtifactStore = Depends(get_store),
    ) -> FileResponse:
        release = database.get_store_release(release_id)
        if release is None or release["yanked_at"]:
            raise HTTPException(status_code=404, detail="Release not found.")
        path = store.blob_path(release["sha256"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact is missing from storage.")
        database.store_event(
            "downloaded",
            package_id=release["package_id"],
            release_id=release_id,
            agent_id=agent["id"],
            detail={"version": release["version"]},
        )
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=release["filename"],
            headers={"X-Checksum-Sha256": release["sha256"]},
        )

    @app.get("/api/store/events")
    def list_store_events(
        limit: int = 100,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        limit = max(1, min(int(limit), 500))
        with database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM store_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return {"events": [dict(row) for row in rows]}

    @app.get("/api/store/stats")
    def store_stats(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        store: ArtifactStore = Depends(get_store),
    ) -> dict[str, Any]:
        del user
        return {**database.store_stats(), "bytes": store.total_bytes(), "max_bytes": store.max_total_bytes}

    @app.get("/api/update/status")
    def update_status(
        force: bool = False,
        user: dict[str, Any] = Depends(require_admin),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del user
        if not config.update_feed:
            return updates.check_update("")
        cache = app.state.update_cache
        now = time.monotonic()
        ttl = config.update_check_hours * 3600
        if force or cache["data"] is None or now - cache["at"] > ttl:
            cache["data"] = updates.check_update(config.update_feed)
            cache["at"] = now
        return cache["data"]

    @app.get("/api/agent/{agent_id}/update")
    def agent_update(
        agent_id: str,
        current_version: str = Query(default=""),
        agent: dict[str, Any] = Depends(agent_auth),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del agent_id
        current = current_version or str(agent.get("agent_version") or "0")
        release = _latest_agent_release(database)
        if release is None:
            return {"available": False, "current_version": current, "reason": "no agent release in the store"}
        if updates.parse_version(release["version"]) <= updates.parse_version(current):
            return {
                "available": False,
                "current_version": current,
                "latest_version": release["version"],
            }
        return {
            "available": True,
            "current_version": current,
            "version": release["version"],
            "release_id": release["id"],
            "sha256": release["sha256"],
            "size": release["size"],
            "filename": release["filename"],
            "channel": release["channel"],
            "min_version": release["min_version"],
            "notes_url": release["notes_url"],
            "download_url": f"/api/agent/{agent['id']}/store/releases/{release['id']}/download",
        }

    @app.get("/api/agents/{agent_id}/packages")
    def get_agent_packages(
        agent_id: str,
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        inventory = database.list_package_inventory(agent_id)
        rules = database.list_package_rules(agent_id)
        managers = [
            {"name": row["manager"], "version": row["version"]}
            for row in inventory
            if row["package"] == "__manager__"
        ]
        packages = [
            {key: row[key] for key in ("manager", "package", "version", "arch", "source", "last_seen")}
            for row in inventory
            if row["package"] != "__manager__"
        ]
        return {
            "inventory": packages,
            "managers": managers,
            "rules": [
                {
                    "manager": row["manager"],
                    "package": row["package"],
                    "desired_state": row["desired_state"],
                    "version_spec": row["version_spec"],
                    "enabled": bool(row["enabled"]),
                }
                for row in rules
            ],
            "drift": _package_actions(packages, rules),
        }

    @app.put("/api/agents/{agent_id}/packages")
    def set_agent_packages(
        agent_id: str,
        payload: list[PackageRuleIn],
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        rules: list[dict[str, Any]] = []
        for item in payload:
            if item.desired_state == "version" and not item.version_spec:
                raise HTTPException(status_code=400, detail="version state requires version_spec.")
            rules.append(item.model_dump())
        database.replace_package_rules(agent_id, rules, user["username"])
        database.audit(
            actor=user["username"],
            action="packages.rules_updated",
            target=agent_id,
            detail={"rules": rules},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"rules": rules}

    @app.post("/api/agents/{agent_id}/packages/sync")
    def sync_agent_packages(
        agent_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_operator),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        with database.connection() as conn:
            agent = conn.execute("SELECT id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found.")
        inventory = database.list_package_inventory(agent_id)
        packages = [
            {key: row[key] for key in ("manager", "package", "version")}
            for row in inventory
            if row["package"] != "__manager__"
        ]
        actions = _package_actions(packages, database.list_package_rules(agent_id))
        if not actions:
            return {"queued": False, "actions": [], "message": "All managed packages are in the desired state."}
        command = queue_command(
            agent_id,
            "package",
            {"actions": actions},
            user,
            request,
            database,
            config,
            audit_action="packages.sync_queued",
        )
        return {"queued": True, "command": command, "actions": actions}

    @app.get("/api/profiles")
    def list_agent_profiles(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        profiles = []
        for row in database.list_profiles():
            profiles.append(
                {
                    "id": row["id"],
                    "slug": row["slug"],
                    "name": row["name"],
                    "description": row["description"],
                    "is_system": bool(row["is_system"]),
                    "revision": int(row["revision"]),
                    "settings": _profile_settings(row),
                    "agent_count": database.count_profile_agents(row["id"]),
                }
            )
        return {"profiles": profiles}

    @app.post("/api/profiles")
    def create_agent_profile(
        payload: AgentProfileIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        settings = payload.settings.model_dump()
        try:
            row = database.create_profile(
                slug=payload.slug,
                name=payload.name,
                description=payload.description,
                settings=settings,
                actor=user["username"],
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="A profile with that slug already exists.")
        database.audit(
            actor=user["username"],
            action="profile.created",
            target=row["id"],
            detail={"slug": payload.slug, "settings": settings},
            ip=client_ip(request, config.trust_proxy),
        )
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "is_system": bool(row["is_system"]),
            "revision": int(row["revision"]),
            "settings": settings,
        }

    @app.patch("/api/profiles/{profile_id}")
    def update_agent_profile(
        profile_id: str,
        payload: AgentProfilePatchIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_profile_by_id(profile_id) or database.get_profile_by_slug(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        settings = payload.settings.model_dump() if payload.settings is not None else None
        row = database.update_profile(
            existing["id"],
            name=payload.name,
            description=payload.description,
            settings=settings,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        database.audit(
            actor=user["username"],
            action="profile.updated",
            target=row["id"],
            detail={"slug": row["slug"], "settings": settings},
            ip=client_ip(request, config.trust_proxy),
        )
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "is_system": bool(row["is_system"]),
            "revision": int(row["revision"]),
            "settings": _profile_settings(row),
        }

    @app.delete("/api/profiles/{profile_id}")
    def delete_agent_profile(
        profile_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_profile_by_id(profile_id) or database.get_profile_by_slug(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        try:
            deleted = database.delete_profile(existing["id"])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if not deleted:
            raise HTTPException(status_code=400, detail="System profiles cannot be deleted.")
        database.audit(
            actor=user["username"],
            action="profile.deleted",
            target=existing["id"],
            detail={"slug": existing["slug"]},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"deleted": True, "id": existing["id"]}

    @app.post("/api/profiles/{profile_id}/reset")
    def reset_agent_profile(
        profile_id: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        existing = database.get_profile_by_id(profile_id) or database.get_profile_by_slug(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
        if not existing["is_system"]:
            raise HTTPException(status_code=400, detail="Only built-in profiles can be reset.")
        default = next((profile for profile in DEFAULT_PROFILES if profile["id"] == existing["id"]), None)
        if default is None:
            raise HTTPException(status_code=400, detail="No built-in defaults for this profile.")
        row = database.update_profile(
            existing["id"],
            name=default["name"],
            description=default["description"],
            settings=default["settings"],
        )
        database.audit(
            actor=user["username"],
            action="profile.reset",
            target=existing["id"],
            detail={"slug": existing["slug"]},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"reset": True, "id": existing["id"], "revision": int(row["revision"]) if row else 0}

    @app.get("/api/profile-tags")
    def list_profile_tags(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return {
            "assignments": [
                {"tag": row["tag"], "profile_id": row["profile_id"], "updated_at": row["updated_at"]}
                for row in database.list_profile_tags()
            ]
        }

    @app.put("/api/profile-tags/{tag}")
    def set_profile_tag(
        tag: str,
        payload: ProfileTagIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        tag = tag.strip()[:64]
        if not tag:
            raise HTTPException(status_code=400, detail="Tag is required.")
        profile = database.get_profile_by_slug(payload.profile) or database.get_profile_by_id(payload.profile)
        if profile is None:
            raise HTTPException(status_code=400, detail="Unknown agent profile.")
        changed = database.set_profile_tag(tag, profile["id"])
        database.audit(
            actor=user["username"],
            action="profile.tag_assigned",
            target=tag,
            detail={"profile_id": profile["id"], "agents": changed},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"tag": tag, "profile_id": profile["id"], "agents": changed}

    @app.delete("/api/profile-tags/{tag}")
    def delete_profile_tag(
        tag: str,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        deleted = database.delete_profile_tag(tag)
        database.audit(
            actor=user["username"],
            action="profile.tag_unassigned",
            target=tag,
            detail={"deleted": deleted},
            ip=client_ip(request, config.trust_proxy),
        )
        return {"deleted": deleted, "tag": tag}

    @app.post("/api/remote-install/test")
    def test_remote_install(
        payload: RemoteInstallIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        config: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        del request, user
        if not config.ssh_install_enabled:
            raise HTTPException(status_code=404, detail="Remote install is disabled.")
        try:
            output = remote_install.test_connection(
                key_path=config.ssh_key_path,
                host=payload.host,
                port=payload.port,
                user=payload.user,
            )
        except remote_install.RemoteInstallError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "output": output}

    @app.post("/api/remote-install")
    def create_remote_install(
        payload: RemoteInstallIn,
        request: Request,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
        store: ArtifactStore = Depends(get_store),
    ) -> dict[str, Any]:
        if not config.ssh_install_enabled:
            raise HTTPException(status_code=404, detail="Remote install is disabled.")
        profile = database.get_profile_by_slug(payload.profile) or database.get_profile_by_id(payload.profile)
        if profile is None:
            raise HTTPException(status_code=400, detail="Unknown agent profile.")
        release = _latest_agent_release(database)
        if release is None:
            raise HTTPException(status_code=400, detail="Publish an agent-wheel release under 'runner-agent' first.")
        wheel_path = store.blob_path(release["sha256"])
        if not wheel_path.is_file():
            raise HTTPException(status_code=400, detail="The agent wheel blob is missing from the store.")
        try:
            remote_install._key_check(config.ssh_key_path)
        except remote_install.RemoteInstallError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if database.running_remote_install_jobs() > 0:
            raise HTTPException(status_code=409, detail="Another remote install is already running. Wait for it to finish.")
        name = (payload.name or payload.host).strip()[:255]
        job = database.create_remote_install_job(
            host=payload.host,
            port=payload.port,
            ssh_user=payload.user,
            profile=profile["slug"],
            name=name,
            watch_services=[str(item)[:255] for item in payload.watch_services if str(item).strip()][:64],
            actor=user["username"],
        )
        token = _issue_remote_enrollment_token(database, user["username"], f"remote-install:{payload.host}")
        server_url = config.public_url or str(request.base_url).rstrip("/")
        database.audit(
            actor=user["username"],
            action="remote_install.created",
            target=job["id"],
            detail={"host": payload.host, "user": payload.user, "profile": profile["slug"]},
            ip=client_ip(request, config.trust_proxy),
        )
        threading.Thread(
            target=_run_remote_install_job,
            args=(config, job["id"], token, str(wheel_path), server_url),
            daemon=True,
        ).start()
        return _remote_job_dict(job)

    @app.get("/api/remote-install")
    def list_remote_install(
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        return {"jobs": [_remote_job_dict(row) for row in database.list_remote_install_jobs()]}

    @app.get("/api/remote-install/{job_id}")
    def get_remote_install(
        job_id: str,
        user: dict[str, Any] = Depends(require_admin),
        database: Database = Depends(get_db),
    ) -> dict[str, Any]:
        del user
        job = database.get_remote_install_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Remote install job not found.")
        return _remote_job_dict(job)

    @app.get("/api/export/agents.csv")
    def export_agents_csv(
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
        config: Settings = Depends(get_settings),
    ) -> Response:
        del user
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["id", "name", "hostname", "os", "os_version", "arch", "ip", "mac", "agent_version", "online", "profile_id", "tags", "last_seen", "service_inventory_at"]
        )
        with database.connection() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY name COLLATE NOCASE").fetchall()
        for row in rows:
            agent = agent_to_dict(row, config)
            writer.writerow(
                [
                    agent["id"],
                    agent["name"],
                    agent["hostname"],
                    agent["os"],
                    agent["os_version"],
                    agent["arch"],
                    agent["ip"],
                    agent["mac"],
                    agent["agent_version"],
                    "yes" if agent["online"] else "no",
                    agent["profile_id"],
                    ",".join(agent["tags"]),
                    agent["last_seen"] or "",
                    agent["service_inventory_at"] or "",
                ]
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="runner-control-agents.csv"'},
        )

    @app.get("/api/export/packages.csv")
    def export_packages_csv(
        agent_id: str = Query(default=""),
        user: dict[str, Any] = Depends(current_user),
        database: Database = Depends(get_db),
    ) -> Response:
        del user
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["agent_id", "agent_name", "manager", "package", "version", "arch", "source", "last_seen"])
        with database.connection() as conn:
            if agent_id:
                rows = conn.execute(
                    """SELECT p.*, a.name AS agent_name FROM package_inventory p
                       JOIN agents a ON a.id = p.agent_id
                       WHERE p.agent_id = ? AND p.package != '__manager__'
                       ORDER BY p.manager, p.package""",
                    (agent_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT p.*, a.name AS agent_name FROM package_inventory p
                       JOIN agents a ON a.id = p.agent_id
                       WHERE p.package != '__manager__'
                       ORDER BY a.name, p.manager, p.package"""
                ).fetchall()
        for row in rows:
            writer.writerow(
                [
                    row["agent_id"],
                    row["agent_name"],
                    row["manager"],
                    row["package"],
                    row["version"],
                    row["arch"],
                    row["source"],
                    row["last_seen"],
                ]
            )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="runner-control-packages.csv"'},
        )

    return app


def _latest_agent_release(database: Database) -> Any:
    package = database.get_store_package("runner-agent")
    if package is None:
        return None
    releases = [
        row
        for row in database.list_store_releases(package["id"])
        if row["artifact_type"] == "agent-wheel" and not row["yanked_at"]
    ]
    if not releases:
        return None
    return max(releases, key=lambda row: updates.parse_version(row["version"]))


def _policy_from_settings(settings: dict[str, Any]) -> dict[str, bool]:
    actions = settings.get("actions") if isinstance(settings.get("actions"), dict) else {}
    return {
        "allow_shell": bool(actions.get("allow_shell", True)),
        "allow_power": bool(actions.get("allow_power", True)),
        "allow_service": bool(actions.get("allow_service", True)),
        "allow_packages": bool(actions.get("allow_packages", True)),
    }


def _profile_settings(row: Any) -> dict[str, Any]:
    settings = loads(row["settings"], {}) if row is not None else {}
    return settings if isinstance(settings, dict) else {}


def _profile_payload(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    settings = _profile_settings(row)
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "revision": int(row["revision"]),
        "is_system": bool(row["is_system"]),
        **settings,
    }


def _remote_job_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "host": row["host"],
        "port": row["port"],
        "user": row["ssh_user"],
        "os": row["os"],
        "profile": row["profile"],
        "name": row["name"],
        "status": row["status"],
        "log": row["log"],
        "error": row["error"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _issue_remote_enrollment_token(database: Database, actor: str, label: str) -> str:
    token = new_token("rc_enroll_")
    token_id = "enr_" + secrets.token_hex(8)
    now = utcnow()
    expires = now + timedelta(minutes=30)
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO enrollment_tokens
               (id, token_hash, label, created_by, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                token_id,
                hash_token(token),
                label,
                actor,
                now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            ),
        )
        database.audit(
            actor=actor,
            action="enrollment.created",
            target=token_id,
            detail={"label": label, "purpose": "remote_install"},
            conn=conn,
        )
    return token


def _run_remote_install_job(
    settings: Settings,
    job_id: str,
    token: str,
    wheel_path: str,
    server_url: str,
) -> None:
    database = Database(settings.database_path)
    row = database.get_remote_install_job(job_id)
    if row is None:
        return
    database.update_remote_install_job(
        job_id,
        status="running",
        started=True,
        log_append=f"Starting remote install on {row['ssh_user']}@{row['host']}:{row['port']}",
    )
    try:
        remote_install.run_install(
            key_path=settings.ssh_key_path,
            host=row["host"],
            port=row["port"],
            user=row["ssh_user"],
            server_url=server_url,
            token=token,
            name=row["name"],
            profile=row["profile"],
            watch_services=loads(row["watch_services"], []),
            wheel_path=wheel_path,
            log=lambda line: database.update_remote_install_job(job_id, log_append=line),
        )
    except Exception as exc:  # job boundary: always record the failure
        database.update_remote_install_job(
            job_id,
            status="failed",
            error=str(exc)[:4000],
            log_append=f"Remote install failed: {str(exc)[:4000]}",
            finished=True,
        )
        database.audit("system", "remote_install.failed", job_id, {"error": str(exc)[:1000]})
        return
    database.update_remote_install_job(
        job_id,
        status="succeeded",
        log_append="Remote install succeeded.",
        finished=True,
    )
    database.audit("system", "remote_install.completed", job_id, {})


def _package_actions(packages: list[dict[str, Any]], rules: list[Any]) -> list[dict[str, str]]:
    installed = {(row["manager"], row["package"]): row.get("version", "") for row in packages}
    actions: list[dict[str, str]] = []
    for rule in rules:
        if not bool(rule["enabled"]):
            continue
        manager = str(rule["manager"])
        package = str(rule["package"])
        state = str(rule["desired_state"])
        version = str(rule["version_spec"] or "")
        current = installed.get((manager, package))
        action = ""
        if state == "absent":
            if current:
                action = "remove"
        elif state == "version":
            if current != version:
                action = "install"
        elif state == "latest":
            action = "upgrade" if current else "install"
        elif state == "present":
            if not current:
                action = "install"
            elif version and current != version:
                action = "install"
        if not action:
            continue
        item = {"manager": manager, "action": action, "package": package}
        if version and action == "install":
            item["version"] = version
        actions.append(item)
        if len(actions) >= 25:
            break
    return actions


def _permissions(user: dict[str, Any]) -> dict[str, bool]:
    return {
        "operate": role_at_least(user, "operator"),
        "admin": role_at_least(user, "admin"),
        "viewer": True,
    }
