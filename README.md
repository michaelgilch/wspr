# wspr

**wspr** is a push-to-talk voice dictation and transcription tool for Linux/X11. 

You hold a hotkey (default Super+Space), speak, and release. The audio is transcribed locally with faster-whisper and routed to the hotkeys configured **sink**.

The default sink uses `xdotool` to type the transcribed text into the focused window (classic dictation behavior).

A second sink, with its own hotkey, opens a Unix socket to a listener. This socket can be used to send the transcription to another application such as a note taker, prompt-feeder, or home automation script.

## Why not use existing software?

For plain dictation, I probably wouldn't. Plenty of tools already exist. However, I couldn't find anything that matched my exact needs:

- **Model choice**. I wanted fully local and accurate. **faster-whisper** is noticably better than some of the alternatives packaged with existing tools.
- **Socket sink**. I could not find a tool that gave me streaming over a Unix socket to _any_ listener. I wanted composability to not re-invent the wheel each time I have an idea.

## Requirements

- **Linux with X11.** wspr grabs global hotkeys through Xlib (`XGrabKey`), so it needs an Xorg session, not Wayland.
- **Python 3.11+** for the standard-library `tomllib` config parser.
- **Python packages** (see `requirements.txt`):
  - `faster-whisper` - local transcription
  - `sounddevice` - microphone capture (needs the **PortAudio** system library)
  - `numpy` - audio buffer handling
  - `python-xlib` - global hotkey grabs and the X event loop
- **System tools:**
  - `xdotool` - types transcripts into the focused window (the `type` sink)
  - `notify-send` (libnotify) - desktop notifications

`xdotool` and `notify-send` are not pip-installable; install them from your distro's package manager. wspr warns at startup if either is missing rather than failing silently.

## Install

The recommended way to install wspr for everyday use is `install.sh`:

```sh
./install.sh
```

It sets everything up under your home directory (no root needed):

| What                | Where                                       |
|:--------------------|:--------------------------------------------|
| App + private venv  | `~/.local/share/wspr/`                      |
| Launcher executable | `~/.local/bin/wspr`                         |
| Default config      | `~/.config/wspr/wspr.toml` (only if absent) |
| Autostart entry     | `~/.config/autostart/wspr.desktop`          |

It creates the venv, installs the dependencies from `requirements.txt`, and (if
`nvidia-smi` is present) adds the CUDA cuBLAS/cuDNN wheels for `device = "cuda"`.
The installer is safe to re-run: it upgrades the code, dependencies, and
autostart entry, but never overwrites an existing config. If you're already in a
graphical session it (re)starts wspr immediately, and the autostart entry launches
it on future logins.

If `~/.local/bin` isn't on your `PATH`, the installer prints a note; add it to
run `wspr` directly.

### Running from source

To run without installing (e.g. for development), use a local venv instead:

```sh
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
python wspr.py
```

## Testing the socket sink

`test_listener.py` is a minimal consumer for the **socket** sink. It binds the
same Unix socket (`$XDG_RUNTIME_DIR/wspr.sock`) that wspr connects to and prints
each transcript it receives, so you can confirm the sink works before wiring up
a real application. It uses only the standard library, so no venv is needed.

You need a hotkey routed to the socket sink. The default `wspr.toml` already
ships one (`super+alt+d`):

```toml
[[hotkeys]]
combo = "super+alt+d"
sink = "socket"
```

Then, in two terminals:

```sh
# terminal 1 - start the listener
python3 test_listener.py
# -> Listening on /run/user/1000/wspr.sock. Ctrl-C to quit.

# terminal 2 - start wspr
wspr            # or: python wspr.py
```

Hold the socket hotkey (`super+alt+d`), speak, and release. The transcript is
sent over the socket and printed by the listener:

```
  received: 'hello from the socket sink'
```

Ctrl-C stops the listener and removes the socket file.

