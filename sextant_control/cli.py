from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import uvicorn

from .config import Settings
from .db import DEFAULT_PROFILES, Database, utcnow
from .models import ProfileSettings
from .notifications import NotificationError, NotificationService
from .security import hash_password, hash_token, new_token, validate_password
from .server import _ensure_bootstrap_admin, create_app
from .store import ArtifactStore


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    problem = validate_password(password)
    if problem:
        raise SystemExit(problem)
    return password


def _db(settings: Settings) -> Database:
    db = Database(settings.database_path)
    db.initialize()
    return db


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    app = create_app(settings)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_keyfile=args.ssl_keyfile,
        ssl_certfile=args.ssl_certfile,
        proxy_headers=settings.trust_proxy,
        access_log=not args.quiet,
        log_level=args.log_level,
    )
    return 0


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    del args
    db = _db(settings)
    _ensure_bootstrap_admin(settings, db)
    print(f"Database: {settings.database_path}")
    if settings.bootstrap_file.exists():
        print(f"Bootstrap credentials: {settings.bootstrap_file}")
    print("Create an enrollment token with: sextant issue-enrollment")
    return 0


def cmd_create_admin(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    if db.get_user_by_name(args.username) is not None:
        print(f"User {args.username!r} already exists.", file=sys.stderr)
        return 1
    password = args.password or _prompt_password()
    problem = validate_password(password)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    db.create_user(
        username=args.username,
        role=args.role,
        password_hash=hash_password(password),
        display_name=args.display_name or args.username,
        source="local",
    )
    db.audit("cli", "user.created", args.username, {"role": args.role})
    print(f"Created {args.role} user {args.username!r}.")
    return 0


def cmd_reset_password(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    row = db.get_user_by_name(args.username)
    if row is None:
        print(f"No such user: {args.username}", file=sys.stderr)
        return 1
    if row["source"] != "local":
        print("AD-backed accounts cannot have a local password set.", file=sys.stderr)
        return 1
    password = args.password or _prompt_password()
    problem = validate_password(password)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    db.revoke_user_sessions(row["id"])
    with db.connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
            (hash_password(password), row["id"]),
        )
    db.audit("cli", "auth.password_reset", args.username, {})
    if args.username.casefold() == settings.admin_user.casefold():
        try:
            settings.bootstrap_file.unlink(missing_ok=True)
        except OSError:
            pass
    print(f"Password reset for {args.username!r}. Existing sessions were revoked.")
    return 0


def cmd_issue_enrollment(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    _ensure_bootstrap_admin(settings, db)
    token = new_token("sextant_enroll_")
    token_id = "enr_" + new_token()[:16]
    now = utcnow()
    expires = now + timedelta(minutes=args.minutes)
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO enrollment_tokens
               (id, token_hash, label, created_by, created_at, expires_at)
               VALUES (?, ?, ?, 'cli', ?, ?)""",
            (
                token_id,
                hash_token(token),
                args.label,
                now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            ),
        )
    db.audit("cli", "enrollment.created", token_id, {"label": args.label})
    base = settings.public_url or "https://YOUR-SERVER"
    print(f"Enrollment token (shown once): {token}")
    print(f"Expires: {expires.isoformat(timespec='seconds')}")
    print(f"Agent command: sextant-agent enroll --server {base} --token {token}")
    return 0


def cmd_notify_test(args: argparse.Namespace, settings: Settings) -> int:
    del args
    db = _db(settings)
    service = NotificationService(db, settings)
    if not service.configured:
        print(
            "Telegram is not configured. Set SEXTANT_TELEGRAM_BOT_TOKEN and "
            "SEXTANT_TELEGRAM_CHAT_ID.",
            file=sys.stderr,
        )
        return 1
    try:
        service.send_test()
    except NotificationError as exc:
        print(f"Telegram delivery failed: {exc}", file=sys.stderr)
        return 1
    db.audit("cli", "notification.test", "telegram", {})
    print("Telegram test notification sent.")
    return 0


def cmd_notify_log(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    rows = db.list_notifications(args.limit)
    if not rows:
        print("No notifications.")
        return 0
    for row in rows:
        print(
            f"{row['at']}  {str(row['status']):<8} {str(row['severity']):<8} "
            f"{row['kind']:<10} {row['title']}"
        )
        if row["attempts"]:
            print(f"    attempts: {row['attempts']}  next: {row['next_attempt_at'] or '-'}")
        if row["error"]:
            print(f"    error: {row['error']}")
    return 0


def cmd_notify_retry(args: argparse.Namespace, settings: Settings) -> int:
    del args
    db = _db(settings)
    count = db.retry_failed_notifications()
    db.audit("cli", "notification.retry", "telegram", {"count": count})
    print(f"Queued {count} failed notification(s) for retry.")
    return 0


def cmd_list_agents(args: argparse.Namespace, settings: Settings) -> int:
    del args
    db = _db(settings)
    with db.connection() as conn:
        rows = conn.execute("SELECT id, name, hostname, os, ip, last_seen FROM agents ORDER BY name").fetchall()
    if not rows:
        print("No agents enrolled.")
        return 0
    for row in rows:
        print(f"{row['id']}  {row['name']:<24} {row['os']:<10} {row['ip']:<16} last_seen={row['last_seen']}")
    return 0


def cmd_store_publish(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    store = ArtifactStore(
        settings.store_dir,
        max_blob_bytes=settings.store_max_blob_mb * 1024 * 1024,
        max_total_bytes=settings.store_max_total_gb * 1024**3,
    )
    store.ensure()
    package = db.get_store_package(args.slug)
    if package is None:
        package = db.create_store_package(
            slug=args.slug,
            name=args.name or args.slug,
            vendor=args.vendor,
            description=args.description,
            kind=args.kind,
            platforms=args.platform or [],
            actor="cli",
        )
    source = Path(args.file)
    sha256, size = store.digest_file(source)
    temp = store.new_temp_path()
    try:
        shutil.copyfile(source, temp)
        store.commit(temp, sha256, size)
        release = db.create_store_release(
            package_id=package["id"],
            version=args.version,
            artifact_type=args.artifact_type,
            filename=args.filename or source.name,
            size=size,
            sha256=sha256,
            manifest={
                "schema": 1,
                "slug": args.slug,
                "version": args.version,
                "artifact_type": args.artifact_type,
                "filename": args.filename or source.name,
                "sha256": sha256,
                "size": size,
                "signature": None,
                "key_id": None,
            },
            actor="cli",
            channel=args.channel,
            min_version=args.min_version,
            mandatory=args.mandatory,
            notes_url=args.notes_url,
        )
    except Exception as exc:  # integrity/duplicate errors surface to the operator
        print(f"Publish failed: {exc}", file=sys.stderr)
        return 1
    finally:
        temp.unlink(missing_ok=True)
    db.store_event("published", package_id=package["id"], release_id=release["id"], detail={"version": args.version}, actor="cli")
    print(f"Published {args.slug} {args.version} ({release['id']})")
    print(f"sha256={sha256} size={size}")
    return 0


def cmd_store_list(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    for package in db.list_store_packages():
        print(f"{package['slug']:<32} {package['name']:<32} kind={package['kind']}")
        for release in db.list_store_releases(package["id"]):
            yanked = " yanked" if release["yanked_at"] else ""
            print(f"  {release['version']:<16} {release['artifact_type']:<12} {release['size']:>12} bytes {release['sha256'][:12]}{yanked}")
    return 0


def cmd_store_yank(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    release = db.get_store_release(args.release_id)
    if release is None:
        print("Release not found.", file=sys.stderr)
        return 1
    db.yank_store_release(args.release_id, args.reason)
    db.store_event("yanked", package_id=release["package_id"], release_id=args.release_id, detail={"reason": args.reason}, actor="cli")
    print(f"Yanked {args.release_id}.")
    return 0


def _load_profile_settings(path: str | None) -> dict | None:
    if not path:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("settings file must contain a JSON object")
    return ProfileSettings(**raw).model_dump()


def cmd_profile_list(args: argparse.Namespace, settings: Settings) -> int:
    del args
    db = _db(settings)
    for profile in db.list_profiles():
        print(
            f"{profile['slug']:<20} {profile['name']:<24} "
            f"system={bool(profile['is_system'])} rev={profile['revision']} "
            f"agents={db.count_profile_agents(profile['id'])}"
        )
    return 0


def cmd_profile_create(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    if args.settings_file:
        settings_data = _load_profile_settings(args.settings_file)
    else:
        source = next((profile for profile in DEFAULT_PROFILES if profile["slug"] == args.from_profile), None)
        if source is None:
            print(f"Unknown source profile {args.from_profile!r}.", file=sys.stderr)
            return 1
        settings_data = ProfileSettings(**source["settings"]).model_dump()
    try:
        profile = db.create_profile(
            slug=args.slug,
            name=args.name,
            description=args.description,
            settings=settings_data,
            actor="cli",
        )
    except Exception as exc:
        print(f"Create failed: {exc}", file=sys.stderr)
        return 1
    print(f"Created profile {profile['slug']} ({profile['id']}).")
    return 0


def cmd_profile_update(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    profile = db.get_profile_by_slug(args.slug)
    if profile is None:
        print("Profile not found.", file=sys.stderr)
        return 1
    settings_data = _load_profile_settings(args.settings_file)
    updated = db.update_profile(
        profile["id"],
        name=args.name,
        description=args.description,
        settings=settings_data,
    )
    if updated is None:
        print("Profile not found.", file=sys.stderr)
        return 1
    print(f"Updated {updated['slug']} to revision {updated['revision']}.")
    return 0


def cmd_profile_reset(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    profile = db.get_profile_by_slug(args.slug)
    if profile is None or not profile["is_system"]:
        print("Only built-in profiles can be reset.", file=sys.stderr)
        return 1
    default = next((item for item in DEFAULT_PROFILES if item["id"] == profile["id"]), None)
    if default is None:
        print("No defaults for this profile.", file=sys.stderr)
        return 1
    db.update_profile(
        profile["id"],
        name=default["name"],
        description=default["description"],
        settings=default["settings"],
    )
    print(f"Reset {profile['slug']} to built-in defaults.")
    return 0


def cmd_profile_delete(args: argparse.Namespace, settings: Settings) -> int:
    db = _db(settings)
    profile = db.get_profile_by_slug(args.slug)
    if profile is None:
        print("Profile not found.", file=sys.stderr)
        return 1
    try:
        deleted = db.delete_profile(profile["id"])
    except ValueError as exc:
        print(f"Delete failed: {exc}", file=sys.stderr)
        return 1
    if not deleted:
        print("System profiles cannot be deleted.", file=sys.stderr)
        return 1
    print(f"Deleted {profile['slug']}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sextant", description="Sextant server")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the web console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--ssl-keyfile", default=None)
    serve.add_argument("--ssl-certfile", default=None)
    serve.add_argument("--quiet", action="store_true", help="Disable HTTP access logging")
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    init = sub.add_parser("init", help="Create the database and bootstrap admin")
    init.set_defaults(func=cmd_init)

    admin = sub.add_parser("create-admin", help="Create a local administrator/operator/viewer")
    admin.add_argument("username")
    admin.add_argument("--role", choices=["admin", "operator", "viewer"], default="admin")
    admin.add_argument("--display-name", default="")
    admin.add_argument("--password", default=None)
    admin.set_defaults(func=cmd_create_admin)

    reset = sub.add_parser("reset-password", help="Reset a local user's password")
    reset.add_argument("username")
    reset.add_argument("--password", default=None)
    reset.set_defaults(func=cmd_reset_password)

    enroll = sub.add_parser("issue-enrollment", help="Issue a one-time agent enrollment token")
    enroll.add_argument("--label", default="cli")
    enroll.add_argument("--minutes", type=int, default=30)
    enroll.set_defaults(func=cmd_issue_enrollment)

    publish = sub.add_parser("store-publish", help="Publish an artifact to the local package store")
    publish.add_argument("--slug", required=True)
    publish.add_argument("--version", required=True)
    publish.add_argument("--file", required=True)
    publish.add_argument(
        "--artifact-type",
        required=True,
        choices=["agent-wheel", "deb", "rpm", "msi", "msix", "zip", "exe"],
    )
    publish.add_argument("--name", default="")
    publish.add_argument("--vendor", default="")
    publish.add_argument("--description", default="")
    publish.add_argument("--kind", choices=["app", "agent", "driver"], default="app")
    publish.add_argument("--platform", action="append", default=[])
    publish.add_argument("--filename", default="")
    publish.add_argument("--channel", choices=["stable", "beta"], default="stable")
    publish.add_argument("--min-version", default="")
    publish.add_argument("--notes-url", default="")
    publish.add_argument("--mandatory", action="store_true")
    publish.set_defaults(func=cmd_store_publish)

    store_list = sub.add_parser("store-list", help="List packages and releases in the local store")
    store_list.set_defaults(func=cmd_store_list)

    yank = sub.add_parser("store-yank", help="Yank a store release")
    yank.add_argument("release_id")
    yank.add_argument("--reason", default="")
    yank.set_defaults(func=cmd_store_yank)

    profile_list = sub.add_parser("profile-list", help="List agent behavior profiles")
    profile_list.set_defaults(func=cmd_profile_list)

    profile_create = sub.add_parser("profile-create", help="Create an agent behavior profile")
    profile_create.add_argument("--slug", required=True)
    profile_create.add_argument("--name", required=True)
    profile_create.add_argument("--description", default="")
    profile_create.add_argument("--from", dest="from_profile", choices=["workstation", "server"], default="workstation")
    profile_create.add_argument("--settings-file", default="")
    profile_create.set_defaults(func=cmd_profile_create)

    profile_update = sub.add_parser("profile-update", help="Update a profile name/description/settings")
    profile_update.add_argument("--slug", required=True)
    profile_update.add_argument("--name", default=None)
    profile_update.add_argument("--description", default=None)
    profile_update.add_argument("--settings-file", default="")
    profile_update.set_defaults(func=cmd_profile_update)

    profile_reset = sub.add_parser("profile-reset", help="Reset a built-in profile to defaults")
    profile_reset.add_argument("--slug", required=True)
    profile_reset.set_defaults(func=cmd_profile_reset)

    profile_delete = sub.add_parser("profile-delete", help="Delete an unassigned custom profile")
    profile_delete.add_argument("--slug", required=True)
    profile_delete.set_defaults(func=cmd_profile_delete)

    agents = sub.add_parser("list-agents", help="List enrolled agents")
    agents.set_defaults(func=cmd_list_agents)

    notify_test = sub.add_parser("notify-test", help="Send a Telegram test notification")
    notify_test.set_defaults(func=cmd_notify_test)

    notify_log = sub.add_parser("notify-log", help="Show the notification outbox")
    notify_log.add_argument("--limit", type=int, default=20)
    notify_log.set_defaults(func=cmd_notify_log)

    notify_retry = sub.add_parser("notify-retry", help="Retry failed Telegram notifications")
    notify_retry.set_defaults(func=cmd_notify_retry)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    try:
        return int(args.func(args, settings))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
