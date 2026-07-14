#!/usr/bin/env bash
# wspr installer - installs wspr for the current user:
#   app + venv  -> ~/.local/share/wspr/         (code and a private venv)
#   executable  -> ~/.local/bin/wspr            (launcher into that venv)
#   config      -> ~/.config/wspr/wspr.toml     (created only if absent)
#   unit        -> ~/.config/systemd/user/wspr.service  (systemd user service)
#
# Safe to re-run: it upgrades code/deps and the unit, but never overwrites an
# existing config.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/wspr"
CONFIG_DIR="$HOME/.config/wspr"
SYSTEMD_DIR="$HOME/.config/systemd/user"
VENV="$APP_DIR/venv"

mkdir -p "$BIN_DIR" "$APP_DIR" "$CONFIG_DIR" "$SYSTEMD_DIR"

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
for module in wspr.py command.py; do
    install -m 0644 "$SRC_DIR/$module" "$APP_DIR/$module"
    echo "Installed app    -> $APP_DIR/$module"
done

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

# systemd user unit ----------------------------------------------------------
# Not enabled: systemd would start it before X exists. The window manager
# starts it after importing DISPLAY/XAUTHORITY into the systemd user
# environment (see README).
install -m 0644 "$SRC_DIR/wspr.service" "$SYSTEMD_DIR/wspr.service"
systemctl --user daemon-reload
echo "Installed unit   -> $SYSTEMD_DIR/wspr.service"

# (Re)start now if we're in a graphical session. The unit is the
# single-instance mechanism: restart replaces any managed copy, and only one
# instance may hold the hotkey grab anyway.
if [ -n "${DISPLAY:-}" ]; then
    systemctl --user import-environment DISPLAY XAUTHORITY
    systemctl --user restart wspr.service && echo "Started wspr."
fi

echo
echo "Done."
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "NOTE: add $BIN_DIR to your PATH to run 'wspr' directly." ;;
esac
echo "  Running: systemctl --user status wspr"
echo "  Logs:    journalctl --user -u wspr -f"
echo "  Stop:    systemctl --user stop wspr"
