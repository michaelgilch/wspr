#!/usr/bin/env bash
# wspr installer - installs wspr for the current user:
#   app + venv  -> ~/.local/share/wspr/         (code and a private venv)
#   executable  -> ~/.local/bin/wspr            (launcher into that venv)
#   config      -> ~/.config/wspr/wspr.toml     (created only if absent)
#   service     -> ~/.config/systemd/user/wspr.service  (systemd user service)
#
# Usage:
#   ./install.sh              core install/upgrade (refreshes wspr-i3 if installed)
#   ./install.sh --with-i3    additionally install the wspr-i3 command plugin
#   ./install.sh --remove-i3  remove the plugin (core install/upgrade still runs)
#   ./install.sh --uninstall  stop the service and remove app, venv, launcher,
#                             and service file; the config is left in place
#
# Safe to re-run. It upgrades code/deps and the service, but never overwrites an
# existing config. An installed plugin is refreshed on every re-run so core
# and plugin never skew; only --remove-i3 removes it.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

WITH_I3=0
REMOVE_I3=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --with-i3)   WITH_I3=1 ;;
        --remove-i3) REMOVE_I3=1 ;;
        --uninstall) UNINSTALL=1 ;;
        *) echo "Unknown option: $arg" >&2
           echo "Usage: $0 [--with-i3 | --remove-i3 | --uninstall]" >&2
           exit 2 ;;
    esac
done
if [ $((WITH_I3 + REMOVE_I3 + UNINSTALL)) -gt 1 ]; then
    echo "ERROR: --with-i3, --remove-i3, and --uninstall are mutually exclusive." >&2
    exit 2
fi

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/wspr"
CONFIG_DIR="$HOME/.config/wspr"
SYSTEMD_DIR="$HOME/.config/systemd/user"
VENV="$APP_DIR/venv"

# uninstall ------------------------------------------------------------------
# Undoes everything the installer creates except the config, which carries
# user customizations and should survive a reinstall; delete
# ~/.config/wspr/wspr.toml manually for a fully clean slate. The whisper model
# cache (~/.cache/huggingface) is shared with other tools and never touched.
if [ "$UNINSTALL" -eq 1 ]; then
    systemctl --user stop wspr.service 2>/dev/null || true
    rm -rf "$APP_DIR"
    echo "Removed app      -> $APP_DIR/"
    rm -f "$BIN_DIR/wspr"
    echo "Removed bin      -> $BIN_DIR/wspr"
    if [ -e "$SYSTEMD_DIR/wspr.service" ]; then
        rm -f "$SYSTEMD_DIR/wspr.service"
        systemctl --user daemon-reload
        echo "Removed service  -> $SYSTEMD_DIR/wspr.service"
    fi
    echo "Config left at $CONFIG_DIR/wspr.toml - delete it manually for a fully clean slate."
    exit 0
fi

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
for module in wspr.py daemon.py; do
    install -m 0644 "$SRC_DIR/$module" "$APP_DIR/$module"
    echo "Installed app    -> $APP_DIR/$module"
done

# wspr-i3 command plugin -----------------------------------------------------
# Installed only on --with-i3, then sticky: a plain re-run refreshes an
# installed plugin so core and plugin never skew. Removed only by --remove-i3.
# Refresh is remove-and-recopy so renamed/dropped plugin files leave no stale
# modules behind.
if [ "$REMOVE_I3" -eq 1 ]; then
    if [ -d "$APP_DIR/wspr_i3" ]; then
        rm -rf "$APP_DIR/wspr_i3"
        echo "Removed plugin   -> $APP_DIR/wspr_i3/"
    else
        echo "Plugin wspr-i3 is not installed; nothing to remove."
    fi
elif [ "$WITH_I3" -eq 1 ] || [ -d "$APP_DIR/wspr_i3" ]; then
    [ "$WITH_I3" -eq 1 ] || echo "Refreshing installed wspr-i3 plugin (remove with --remove-i3)."
    rm -rf "$APP_DIR/wspr_i3"
    mkdir -p "$APP_DIR/wspr_i3"
    install -m 0644 "$SRC_DIR"/wspr_i3/*.py "$APP_DIR/wspr_i3/"
    echo "Installed plugin -> $APP_DIR/wspr_i3/"

    # Plugin runtime deps: absent ones don't stop the install, but command
    # routing (Ollama), confirmations (rofi), and i3 control (i3-msg) need
    # them. The confirm gate fails closed without rofi: privileged commands
    # become impossible to approve.
    command -v rofi >/dev/null 2>&1 \
        || echo "WARNING: rofi not found - command confirmations need it and fail closed without it."
    command -v i3-msg >/dev/null 2>&1 \
        || echo "WARNING: i3-msg not found - the plugin controls i3 through it."
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 2 "http://localhost:11434/" >/dev/null 2>&1 \
            || echo "WARNING: Ollama not reachable at http://localhost:11434 - command routing needs it ([ollama] url in wspr.toml)."
    else
        echo "NOTE: curl not found - skipped the Ollama reachability check."
    fi
fi

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
