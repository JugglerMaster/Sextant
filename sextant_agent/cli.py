from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

from .agent import AgentRunner
from .client import AgentClient, AgentHTTPError
from .collectors import enrollment_identity
from .config import AgentConfig, default_config_path, load_config, save_config
from . import selfupdate


def _configure_logging(level: str, log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, mode="a", encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def cmd_enroll(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else default_config_path()
    watch = list(args.watch_service or [])
    tags = list(args.tag or [])
    temporary = AgentConfig(
        server_url=args.server.rstrip("/"),
        agent_id="pending",
        agent_token="pending",
        name=args.name or socket.gethostname(),
        verify_tls=not args.insecure,
        ca_file=args.ca_file or "",
        interval=args.interval,
        inventory_interval=getattr(args, "inventory_interval", 3600),
        watch_services=watch,
        tags=tags,
        profile=getattr(args, "profile", "workstation"),
        allow_packages=not bool(getattr(args, "no_packages", False)),
        allow_self_update=not bool(getattr(args, "no_self_update", False)),
        allow_docker=bool(getattr(args, "allow_docker", False)),
        allow_docker_exec=bool(getattr(args, "allow_docker_exec", False)),
        allow_docker_compose=bool(getattr(args, "allow_docker_compose", False)),
        allow_docker_prune=bool(getattr(args, "allow_docker_prune", False)),
        docker_socket=getattr(args, "docker_socket", "") or "unix:///var/run/docker.sock",
        docker_command=getattr(args, "docker_command", "") or "docker",
        docker_timeout=int(getattr(args, "docker_timeout", 60)),
        docker_inventory_interval=int(getattr(args, "docker_inventory_interval", 300)),
        docker_compose_dir=getattr(args, "docker_compose_dir", "") or "/var/lib/sextant-agent/docker/stacks",
    )
    identity = enrollment_identity(temporary)
    client = AgentClient(temporary)
    try:
        response = client.enroll(args.token, identity)
    except AgentHTTPError as exc:
        print(f"Enrollment failed: {exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Enrollment failed: {exc}", file=sys.stderr)
        return 1

    config = AgentConfig(
        server_url=args.server.rstrip("/"),
        agent_id=str(response["agent_id"]),
        agent_token=str(response["agent_token"]),
        name=args.name or identity["hostname"],
        verify_tls=not args.insecure,
        ca_file=args.ca_file or "",
        interval=args.interval,
        inventory_interval=getattr(args, "inventory_interval", 3600),
        watch_services=watch,
        tags=tags,
        profile=getattr(args, "profile", "workstation"),
        allow_packages=not bool(getattr(args, "no_packages", False)),
        allow_self_update=not bool(getattr(args, "no_self_update", False)),
        allow_docker=bool(getattr(args, "allow_docker", False)),
        allow_docker_exec=bool(getattr(args, "allow_docker_exec", False)),
        allow_docker_compose=bool(getattr(args, "allow_docker_compose", False)),
        allow_docker_prune=bool(getattr(args, "allow_docker_prune", False)),
        docker_socket=getattr(args, "docker_socket", "") or "unix:///var/run/docker.sock",
        docker_command=getattr(args, "docker_command", "") or "docker",
        docker_timeout=int(getattr(args, "docker_timeout", 60)),
        docker_inventory_interval=int(getattr(args, "docker_inventory_interval", 300)),
        docker_compose_dir=getattr(args, "docker_compose_dir", "") or "/var/lib/sextant-agent/docker/stacks",
    )
    save_config(config, config_path)
    print(f"Enrolled as {config.agent_id} ({config.name}).")
    print(f"Config saved to {config_path} (mode 0600 where supported).")
    if args.server.startswith("http://"):
        print("warning: this enrollment uses plain HTTP. Use HTTPS on any untrusted network.", file=sys.stderr)
    print("Next: install the systemd unit or Windows scheduled task, then start the agent.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    override = getattr(args, "inventory_interval", None)
    if override is not None:
        config.inventory_interval = override
        config.validate()
    runner = AgentRunner(config)
    return runner.run(once=args.once)


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = AgentClient(config)
    try:
        health = client.health()
    except Exception as exc:
        print(f"Health check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"server": config.server_url, "health": health}, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        info = selfupdate.check(config)
    except Exception as exc:
        print(f"Update check failed: {exc}", file=sys.stderr)
        return 1
    print(selfupdate.report(info))
    if not args.apply:
        print(json.dumps(info, indent=2))
        return 0
    install_dir = Path(args.install_dir) if args.install_dir else None
    result = selfupdate.apply_update(
        config,
        info,
        install_dir=install_dir,
        config_path=Path(args.config) if args.config else None,
        restart=bool(args.restart),
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in {"switched", "staged", "current"} else 1


def cmd_show(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(json.dumps(config.redacted(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sextant-agent", description="Sextant cross-platform agent")
    parser.add_argument("--config", default=None, help="Path to agent config JSON")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-file", default=None, help="Append logs to this file")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="Path to agent config JSON")
    common.add_argument("--log-level", default=argparse.SUPPRESS)
    common.add_argument("--log-file", default=argparse.SUPPRESS, help="Append logs to this file")

    enroll = sub.add_parser("enroll", parents=[common], help="Enroll this host with a one-time token")
    enroll.add_argument("--server", required=True, help="Server base URL, e.g. https://ops.example.com")
    enroll.add_argument("--token", required=True, help="One-time enrollment token")
    enroll.add_argument("--name", default="", help="Display name (defaults to hostname)")
    enroll.add_argument("--insecure", action="store_true", help="Do not verify the server TLS certificate")
    enroll.add_argument("--ca-file", default="", help="Private CA bundle for TLS verification")
    enroll.add_argument("--interval", type=int, default=30)
    enroll.add_argument(
        "--inventory-interval",
        type=int,
        default=3600,
        help="Seconds between slow inventory refreshes (60-86400, default 3600)",
    )
    enroll.add_argument("--watch-service", action="append", default=[], help="Service to monitor (repeatable)")
    enroll.add_argument("--tag", action="append", default=[], help="Agent tag (repeatable)")
    enroll.add_argument("--profile", default="workstation", help="Agent profile slug (default workstation)")
    enroll.add_argument("--allow-packages", action="store_true", help="Deprecated: package management is enabled by default")
    enroll.add_argument("--no-packages", action="store_true", help="Disable managed OS package actions locally")
    enroll.add_argument("--no-self-update", action="store_true", help="Disable agent self-update in the config")
    enroll.add_argument("--allow-docker", action="store_true", help="Enable read-only Docker discovery and inventory")
    enroll.add_argument("--allow-docker-exec", action="store_true", help="Reserved for Docker exec/console support")
    enroll.add_argument("--allow-docker-compose", action="store_true", help="Reserved for Docker Compose support")
    enroll.add_argument("--allow-docker-prune", action="store_true", help="Reserved for Docker prune support")
    enroll.add_argument("--docker-socket", default="", help="Docker socket or DOCKER_HOST (default unix:///var/run/docker.sock)")
    enroll.add_argument("--docker-command", default="", help="Docker CLI command/path (default docker)")
    enroll.add_argument("--docker-timeout", type=int, default=60)
    enroll.add_argument("--docker-inventory-interval", type=int, default=300)
    enroll.add_argument("--docker-compose-dir", default="", help="Agent-owned Compose runtime directory")
    enroll.set_defaults(func=cmd_enroll)

    run = sub.add_parser("run", parents=[common], help="Run the heartbeat/command loop")
    run.add_argument("--once", action="store_true", help="Run one cycle and exit (for diagnostics)")
    run.add_argument(
        "--inventory-interval",
        type=int,
        default=argparse.SUPPRESS,
        help="Override seconds between slow inventory refreshes (60-86400)",
    )
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", parents=[common], help="Check server reachability with the saved config")
    check.set_defaults(func=cmd_check)

    update = sub.add_parser("update", parents=[common], help="Check for or apply an agent update from the store")
    update.add_argument("--apply", action="store_true", help="Download, verify, stage, and switch to the new version")
    update.add_argument("--restart", action="store_true", help="Restart sextant-agent after switching (systemd)")
    update.add_argument("--install-dir", default="", help="Agent install directory (default /opt/sextant-agent)")
    update.set_defaults(func=cmd_update)

    show = sub.add_parser("show", parents=[common], help="Show the saved config with the token redacted")
    show.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level, args.log_file)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
