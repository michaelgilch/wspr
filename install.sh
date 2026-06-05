#!/usr/bin/env bash
# wspr installer — installs wspr for the current user:
#   app + venv  -> ~/.local/share/wspr/         (code and a private venv)
#   executable  -> ~/.local/bin/wspr            (launcher into that venv)
#   config      -> ~/.config/wspr/wspr.toml     (created only if absent)
#   service     -> ~/.config/systemd/user/wspr.service
#
# Safe to re-run: it upgrades code/deps and the service, but never overwrites
# an existing config.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/wspr"
CONFIG_DIR="$HOME/.config/wspr"
UNIT_DIR="$HOME/.config/systemd/user"
VENV="$APP_DIR/venv"

mkdir -p "$BIN_DIR" "$APP_DIR" "$CONFIG_DIR" "$UNIT_DIR"

DEPS=(faster-whisper numpy sounddevice python-xlib)

# venv + dependencies -------------------------------------------------------
echo "Setting up venv at $VENV ..."
if command -v uv >/dev/null 2>&1; then
    [ -d "$VENV" ] || uv venv "$VENV"
    uv pip install --python "$VENV/bin/python" "${DEPS[@]}"
else
    [ -d "$VENV" ] || python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/pip" install "${DEPS[@]}"
fi

# xdotool (runtime dependency, not pip-installable) -------------------------
# wspr types transcriptions with xdotool; without it the service runs but
# silently produces no output.
command -v xdotool >/dev/null 2>&1 \
    || echo "WARNING: xdotool not found — install it (e.g. sudo pacman -S xdotool)."

# application code ----------------------------------------------------------
install -m 0644 "$SRC_DIR/wspr.py" "$APP_DIR/wspr.py"
echo "Installed app    -> $APP_DIR/wspr.py"

# launcher executable -------------------------------------------------------
cat > "$BIN_DIR/wspr" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$APP_DIR/wspr.py" "\$@"
EOF
chmod +x "$BIN_DIR/wspr"
echo "Installed bin    -> $BIN_DIR/wspr"

# default config (only if absent) -------------------------------------------
if [ -e "$CONFIG_DIR/wspr.toml" ]; then
    echo "Config exists    -> $CONFIG_DIR/wspr.toml (left untouched)"
else
    install -m 0644 "$SRC_DIR/wspr.toml" "$CONFIG_DIR/wspr.toml"
    echo "Installed config -> $CONFIG_DIR/wspr.toml"
fi

# systemd user service ------------------------------------------------------
install -m 0644 "$SRC_DIR/wspr.service" "$UNIT_DIR/wspr.service"
echo "Installed service -> $UNIT_DIR/wspr.service"

systemctl --user daemon-reload
# Make the graphical environment (DISPLAY/XAUTHORITY) visible to the service.
systemctl --user import-environment DISPLAY XAUTHORITY 2>/dev/null || true
systemctl --user enable wspr.service >/dev/null
echo "Enabled wspr.service (starts with your graphical session)."

# Start now if we're in a graphical session.
if [ -n "${DISPLAY:-}" ]; then
    systemctl --user restart wspr.service && echo "Started wspr.service."
fi

echo
echo "Done."
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "NOTE: add $BIN_DIR to your PATH to run 'wspr' directly." ;;
esac
echo "  Status: systemctl --user status wspr.service"
echo "  Logs:   journalctl --user -u wspr.service -f"
echo "  Stop:   systemctl --user stop wspr.service"
