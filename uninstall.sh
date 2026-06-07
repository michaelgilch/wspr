#!/usr/bin/env bash
# wspr uninstaller — reverses install.sh for the current user.
# Leaves your config (~/.config/wspr) in place unless --purge is given.
set -euo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/wspr"
CONFIG_DIR="$HOME/.config/wspr"
AUTOSTART_DIR="$HOME/.config/autostart"

# --- stop instance + autostart entry ---------------------------------------
pkill -f "$APP_DIR/wspr.py" 2>/dev/null || true
rm -f "$AUTOSTART_DIR/wspr.desktop"
echo "Stopped wspr and removed autostart entry."

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
