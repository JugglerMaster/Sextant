#!/usr/bin/env bash
# Install and enroll a Sextant agent on a systemd Linux host.
# Run as root from a checked-out/published source tree:
#   sudo ./deploy/install-linux-agent.sh https://ops.example.com <TOKEN> [NAME]
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

SERVER="${1:-}"
TOKEN="${2:-}"
NAME="${3:-$(hostname)}"
if [[ -z "$SERVER" || -z "$TOKEN" ]]; then
  echo "Usage: $0 <server-url> <enrollment-token> [name]" >&2
  exit 1
fi

SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/sextant-agent}"
CONFIG_DIR="${CONFIG_DIR:-/etc/sextant-agent}"
CONFIG_FILE="${CONFIG_FILE:-$CONFIG_DIR/config.json}"
PROFILE="${PROFILE:-workstation}"

if [[ -n "${SERVICE_FILE:-}" ]]; then
  :
elif [[ -f "$SOURCE_DIR/deploy/sextant-agent.service" ]]; then
  SERVICE_FILE="$SOURCE_DIR/deploy/sextant-agent.service"
elif [[ -f "$SCRIPT_DIR/deploy/sextant-agent.service" ]]; then
  SERVICE_FILE="$SCRIPT_DIR/deploy/sextant-agent.service"
elif [[ -f "$SCRIPT_DIR/../deploy/sextant-agent.service" ]]; then
  SERVICE_FILE="$SCRIPT_DIR/../deploy/sextant-agent.service"
else
  echo "Could not find deploy/sextant-agent.service. Set SERVICE_FILE explicitly." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if ! python3 -c 'import venv' >/dev/null 2>&1; then
  echo "python3-venv is required (for Debian/Ubuntu: apt install python3-venv)." >&2
  exit 1
fi

install -d -m 0755 "$INSTALL_DIR"
install -d -m 0700 "$CONFIG_DIR"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --no-deps "$SOURCE_DIR"
ln -sfn .venv "$INSTALL_DIR/current"

ENROLL_ARGS=(
  enroll
  --config "$CONFIG_FILE"
  --server "$SERVER"
  --token "$TOKEN"
  --name "$NAME"
  --profile "$PROFILE"
)
if [[ -n "${CA_FILE:-}" ]]; then
  ENROLL_ARGS+=(--ca-file "$CA_FILE")
fi
if [[ -n "${WATCH_SERVICES:-}" ]]; then
  IFS=',' read -r -a services <<< "$WATCH_SERVICES"
  for service in "${services[@]}"; do
    if [[ -n "$service" ]]; then
      ENROLL_ARGS+=(--watch-service "$service")
    fi
  done
fi
if [[ -n "${AGENT_TAGS:-}" ]]; then
  IFS=',' read -r -a tags <<< "$AGENT_TAGS"
  for tag in "${tags[@]}"; do
    if [[ -n "$tag" ]]; then
      ENROLL_ARGS+=(--tag "$tag")
    fi
  done
fi
if [[ "${ALLOW_PACKAGES:-1}" == "1" ]]; then
  ENROLL_ARGS+=(--allow-packages)
else
  ENROLL_ARGS+=(--no-packages)
fi
if [[ "${ALLOW_DOCKER:-0}" == "1" ]]; then
  ENROLL_ARGS+=(--allow-docker)
fi
if [[ "${ALLOW_DOCKER_EXEC:-0}" == "1" ]]; then
  ENROLL_ARGS+=(--allow-docker-exec)
fi
if [[ "${ALLOW_DOCKER_COMPOSE:-0}" == "1" ]]; then
  ENROLL_ARGS+=(--allow-docker-compose)
fi
if [[ "${ALLOW_DOCKER_PRUNE:-0}" == "1" ]]; then
  ENROLL_ARGS+=(--allow-docker-prune)
fi
if [[ -n "${DOCKER_SOCKET:-}" ]]; then
  ENROLL_ARGS+=(--docker-socket "$DOCKER_SOCKET")
fi
if [[ -n "${DOCKER_COMMAND:-}" ]]; then
  ENROLL_ARGS+=(--docker-command "$DOCKER_COMMAND")
fi
if [[ -n "${DOCKER_COMPOSE_DIR:-}" ]]; then
  ENROLL_ARGS+=(--docker-compose-dir "$DOCKER_COMPOSE_DIR")
fi
if [[ "${NO_SELF_UPDATE:-0}" == "1" ]]; then
  ENROLL_ARGS+=(--no-self-update)
fi

"$INSTALL_DIR/.venv/bin/sextant-agent" "${ENROLL_ARGS[@]}"
chmod 0600 "$CONFIG_FILE"
chown root:root "$CONFIG_FILE"

install -m 0644 "$SERVICE_FILE" /etc/systemd/system/sextant-agent.service
systemctl daemon-reload
systemctl enable --now sextant-agent.service

echo "Agent installed and started. Inspect logs with: journalctl -u sextant-agent -f"
