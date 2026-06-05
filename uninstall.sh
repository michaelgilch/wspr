#!/usr/bin/env bash
# wspr uninstaller — reverses install.sh for the current user.
# Leaves your config (~/.config/wspr) in place unless --purge is given.
set -euo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/wspr"
CONFIG_DIR="$HOME/.config/wspr"
UNIT_DIR="$HOME/.config/systemd/user"

# --- service ---------------------------------------------------------------
systemctl --user disable --now wspr.service 2>/dev/null || true
rm -f "$UNIT_DIR/wspr.service"
systemctl --user daemon-reload 2>/dev/null || true
echo "Removed service."

# --- executable + app ------------------------------------------------------
rm -f "$BIN_DIR/wspr"
rm -rf "$APP_DIR"
echo "Removed launcher and app (incl. venv)."

# --- config (only with --purge) --------------------------------------------
if [ "$PURGE" -eq 1 ]; then
    rm -rf "$CONFIG_DIR"
    echo "Purged config at $CONFIG_DIR."
else
    echo "Left config at $CONFIG_DIR (pass --purge to remove)."
fi

echo "Done."
