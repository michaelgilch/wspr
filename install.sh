#!/usr/bin/env bash
# wspr installer - installs wspr for the current user:
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

# venv + dependencies -------------------------------------------------------
# Runtime deps live in requirements.txt (the source of truth). On NVIDIA
# machines we also install the CUDA 12 cuBLAS/cuDNN wheels: ctranslate2 dlopens
# them at transcribe time and distro CUDA packages may only ship newer sonames
# (e.g. Arch's cuda 13 -> libcublas.so.13). The launcher puts the wheels on
# LD_LIBRARY_PATH.
CUDA_DEPS=()
if command -v nvidia-smi >/dev/null 2>&1; then
    CUDA_DEPS+=(nvidia-cublas-cu12 nvidia-cudnn-cu12)
fi

echo "Setting up venv at $VENV ..."
if command -v uv >/dev/null 2>&1; then
    [ -d "$VENV" ] || uv venv "$VENV"
    uv pip install --python "$VENV/bin/python" -r "$SRC_DIR/requirements.txt"
    [ ${#CUDA_DEPS[@]} -eq 0 ] || uv pip install --python "$VENV/bin/python" "${CUDA_DEPS[@]}"
else
    [ -d "$VENV" ] || python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/pip" install -r "$SRC_DIR/requirements.txt"
    [ ${#CUDA_DEPS[@]} -eq 0 ] || "$VENV/bin/pip" install "${CUDA_DEPS[@]}"
fi

# system tools (runtime deps, not pip-installable) --------------------------
# wspr types transcriptions with xdotool and reports status with notify-send;
# without them wspr still runs but silently produces no output / no notices.
command -v xdotool >/dev/null 2>&1 \
    || echo "WARNING: xdotool not found - install it (e.g. sudo pacman -S xdotool)."
command -v notify-send >/dev/null 2>&1 \
    || echo "WARNING: notify-send not found - install libnotify for desktop notifications."

# application code ----------------------------------------------------------
install -m 0644 "$SRC_DIR/wspr.py" "$APP_DIR/wspr.py"
echo "Installed app    -> $APP_DIR/wspr.py"

# launcher executable -------------------------------------------------------
# The loop puts the pip-installed CUDA 12 cuBLAS/cuDNN wheels (if present) on
# LD_LIBRARY_PATH - ctranslate2 does not find them on its own. Harmless no-op
# on CPU-only installs where the nvidia/ directories don't exist.
cat > "$BIN_DIR/wspr" <<EOF
#!/usr/bin/env bash
for libdir in "$VENV"/lib/python*/site-packages/nvidia/{cublas,cudnn}/lib; do
    [ -d "\$libdir" ] && LD_LIBRARY_PATH="\${LD_LIBRARY_PATH:+\$LD_LIBRARY_PATH:}\$libdir"
done
export LD_LIBRARY_PATH
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
Comment=Push-to-talk voice dictation (hold Super+Space to dictate)
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
