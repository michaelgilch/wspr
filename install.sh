#!/usr/bin/env bash
# wspr installer — installs wspr for the current user:
#   app + venv  -> ~/.local/share/wspr/         (code and a private venv)
#   executable  -> ~/.local/bin/wspr            (launcher into that venv)
#   config      -> ~/.config/wspr/wspr.toml     (created only if absent)
#   autostart   -> ~/.config/autostart/wspr.desktop  (XDG autostart entry)
#
# Safe to re-run: it upgrades code/deps and the autostart entry, but never
# overwrites an existing config.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/wspr"
CONFIG_DIR="$HOME/.config/wspr"
AUTOSTART_DIR="$HOME/.config/autostart"
VENV="$APP_DIR/venv"

mkdir -p "$BIN_DIR" "$APP_DIR" "$CONFIG_DIR" "$AUTOSTART_DIR"

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
# wspr types transcriptions with xdotool; without it wspr runs but silently
# produces no output.
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

# autostart entry -----------------------------------------------------------
# Launched by the XDG autostart mechanism (e.g. dex) with the graphical
# session, so it inherits DISPLAY/XAUTHORITY directly from the session.
cat > "$AUTOSTART_DIR/wspr.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=wspr
Comment=Push-to-talk voice dictation (hold Super+F1 to dictate)
Exec=$BIN_DIR/wspr
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
echo "Installed autostart -> $AUTOSTART_DIR/wspr.desktop"

# (Re)start now if we're in a graphical session. Only one instance may hold the
# hotkey grab, so stop any running copy first.
if [ -n "${DISPLAY:-}" ]; then
    pkill -f "$APP_DIR/wspr.py" 2>/dev/null || true
    setsid -f "$BIN_DIR/wspr" >/dev/null 2>&1 && echo "Started wspr."
fi

echo
echo "Done."
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "NOTE: add $BIN_DIR to your PATH to run 'wspr' directly." ;;
esac
echo "  Running: pgrep -af wspr.py"
echo "  Logs:    run 'wspr' in a terminal to see its output"
echo "  Stop:    pkill -f wspr.py"
