# wspr

Push-to-talk voice dictation. Hold a hotkey (default **Super+F1**), speak,
release — the audio is transcribed locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and typed into
whatever window has focus.

Designed for **X11**: text is injected via `xdotool`.

## Requirements

- Python 3.11+ (3.14 recommended; uses the stdlib `tomllib`)
- An X11 session
- `xdotool` for typing into the focused window:
- A working microphone (PortAudio, pulled in by `sounddevice`)

## Install (recommended)

`./install.sh` installs wspr for the current user and registers an XDG
**autostart** entry that starts it with your graphical session:

It lays things out like this:

| What | Location |
|------|----------|
| App code + private venv | `~/.local/share/wspr/` |
| Launcher executable | `~/.local/bin/wspr` |
| Config | `~/.config/wspr/wspr.toml` (created only if absent) |
| XDG autostart entry | `~/.config/autostart/wspr.desktop` |

The installer is safe to re-run — it upgrades the code, dependencies, and
autostart entry, but never overwrites an existing config. Make sure
`~/.local/bin` is on your `PATH` to run `wspr` directly.

### Managing wspr

> wspr grabs the hotkey globally, so only **one** instance can run at a time.
> Stop the running copy before launching a dev copy from the repo (below):
>
> ```bash
> pkill -f wspr.py        # stop the running instance
> pgrep -af wspr.py       # check whether it's running
> ```

### Uninstall

```bash
./uninstall.sh            # remove app + autostart entry, keep config
./uninstall.sh --purge    # also remove ~/.config/wspr
```

## Running from the repo (development)

To run directly from a checkout without installing, create a local venv and run
the script:

```bash
uv venv .venv
uv pip install --python .venv/bin/python faster-whisper numpy sounddevice python-xlib
./.venv/bin/python wspr.py
```

On first run the configured model is downloaded to your Hugging Face cache.
Then:

1. **Hold** the hotkey (default **Super+F1**) and speak.
2. **Release** it — wspr transcribes the audio.
3. The text is typed into the focused window.

Press `Ctrl-C` to quit.

## Configuration

Settings live in a TOML file. wspr looks for one in this order and uses the
**first that exists**:

| Priority | Location | Purpose |
|----------|----------|---------|
| 1 | `$WSPR_CONFIG` | Explicit override — point it at any file: `WSPR_CONFIG=~/my.toml ./.venv/bin/python wspr.py`. Used as-is even if it doesn't exist (then defaults apply). |
| 2 | `./wspr.toml` (next to `wspr.py`) | The repo default. The common case. |
| 3 | `~/.config/wspr/wspr.toml` | Per-user / OS-installed location (XDG). |
| — | *(none found)* | Built-in defaults are used. wspr never writes a config file. |

Higher priority wins: `$WSPR_CONFIG` overrides the repo file, which overrides
the XDG file. The search stops at the first match.

### Options

`wspr.toml` ships with these defaults:

```toml
[hotkey]
# Press-and-hold combo. Modifiers: super, ctrl, alt, shift.
# Trigger: a function key (f1-f20), a named key (space, enter, tab, esc,
# backspace), or a single character. Examples: "super+f1", "ctrl+alt+space",
# "f9".
combo = "super+f1"

[model]
size = "small.en"     # tiny.en / base.en / small.en / medium / large-v3
device = "cpu"        # cpu / cuda
compute_type = "int8" # int8 (CPU) / float16 (GPU)
```

Edit the file and restart wspr — no code changes needed. A larger `size`
(e.g. `medium`) improves accuracy at the cost of speed; a smaller one
(`base.en`, `tiny.en`) is faster. `device = "cuda"` with
`compute_type = "float16"` runs on a GPU.

### CUDA

ctranslate2 (faster-whisper's engine) needs CUDA 12's cuBLAS and cuDNN 9 at
runtime, which distro CUDA packages often don't provide (e.g. Arch's `cuda 13`
only ships `libcublas.so.13`). `install.sh` handles this automatically on
machines with an NVIDIA GPU: it installs the `nvidia-cublas-cu12` and
`nvidia-cudnn-cu12` wheels into the venv and the launcher puts them on
`LD_LIBRARY_PATH`. For a dev checkout, do the same by hand:

```bash
uv pip install --python .venv/bin/python nvidia-cublas-cu12 nvidia-cudnn-cu12
sp=$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')
LD_LIBRARY_PATH="$sp/nvidia/cublas/lib:$sp/nvidia/cudnn/lib" ./.venv/bin/python wspr.py
```

The audio format (16 kHz mono) and transcription language (English) are fixed
in the code — both are requirements of the `.en` Whisper models — so they are
not configurable.

## Files

| File | Purpose |
|------|---------|
| `wspr.py` | The dictation engine. |
| `wspr.toml` | Default configuration (shipped with the repo). |
| `install.sh` | Installs wspr for the current user (venv, launcher, config, autostart entry). |
| `uninstall.sh` | Reverses the install (`--purge` also removes config). |
