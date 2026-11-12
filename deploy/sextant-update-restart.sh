#!/bin/sh
# Root-owned restart handoff for Sextant self-update.
# Installed as /usr/local/sbin/sextant-update-restart (mode 0755, root:root).
# The unprivileged server writes /var/lib/sextant/restart-request; the
# systemd path unit invokes this helper outside the server's cgroup.
set -eu

TRIGGER="${SEXTANT_RESTART_TRIGGER:-/var/lib/sextant/restart-request}"
UNIT="${SEXTANT_RESTART_UNIT:-sextant.service}"

if [ ! -e "$TRIGGER" ]; then
    exit 0
fi

# The trigger contains only version/action metadata, never credentials.
if command -v logger >/dev/null 2>&1; then
    logger -t sextant-update "restart requested for $UNIT"
fi
rm -f "$TRIGGER"

exec systemctl restart "$UNIT"
