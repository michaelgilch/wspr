# wspr

**wspr** is a push-to-talk voice dictation and transcription tool for Linux/X11.

It provides local voice-to-text as well as voice-to-command (currently for i3-wm) and a socket interface for piping transcribed text to a listener.

You hold a hotkey (default Super+Space), speak, and release. The audio is transcribed locally with faster-whisper and routed to the hotkey's configured **sink**:

- **type** - types the transcript into the focused window with `xdotool` (classic dictation behavior).
- **socket** - sends the transcript over a Unix socket to any listener: a note taker, prompt-feeder, or home automation script.
- **command** - hands the transcript to a **command plugin**, which routes it through a local LLM (Ollama) onto a whitelisted window-manager action, validates it, and executes it. The i3 plugin, **wspr-i3**, ships in this repo as `wspr_i3/`.

The core daemon is window-manager-agnostic. It knows hotkeys, audio, and transcription. All i3-command processing lives in the plugin, which core loads by name from config; dictation-only setups never import it.

## Why not use existing software?

For plain dictation, I probably wouldn't. Plenty of tools already exist. However, I couldn't find anything that matched my exact needs or interests.

- **Model choice**. I wanted fully local and accurate. **faster-whisper** is noticably better than some of the alternatives packaged with existing tools.
- **Socket sink**. My original plan was to control i3 through a socket. I could not find a tool that gave me streaming over a Unix socket to _any_ listener. I wanted composability to not re-invent the wheel each time I have an idea. I've left the socket functionality, but have move onto plugin-based voice commands.
- **Voice commands**. I wanted the same push-to-talk flow to drive my window manager through a local LLM, with a whitelist between the model and my machine.

## Requirements

- **Linux with X11.** wspr grabs global hotkeys through Xlib (`XGrabKey`), so it needs an Xorg session, not Wayland.
- **Python 3.11+** for the standard-library `tomllib` config parser.
- **Python packages** (see `requirements.txt`), all for the dictation core; the command plugin is stdlib-only:
  - `faster-whisper` - local transcription
  - `sounddevice` - microphone capture (needs the **PortAudio** system library)
  - `numpy` - audio buffer handling
  - `python-xlib` - global hotkey grabs and the X event loop
- **System tools:**
  - `xdotool` - types transcripts into the focused window (the `type` sink)
  - `notify-send` (libnotify) - desktop notifications
- **For the command sink** (wspr-i3 plugin only; dictation works without these):
  - **i3** - actions execute through `i3-msg`
  - **Ollama** running locally with the configured routing model (default `gemma3:1b`)

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
| wspr-i3 plugin      | `~/.local/share/wspr/wspr_i3/`              |
| Launcher executable | `~/.local/bin/wspr`                         |
| Default config      | `~/.config/wspr/wspr.toml` (only if absent) |
| systemd user unit   | `~/.config/systemd/user/wspr.service`       |

It creates the venv, installs the dependencies from `requirements.txt`, and (if
`nvidia-smi` is present) adds the CUDA cuBLAS/cuDNN wheels for `device = "cuda"`.
The installer is safe to re-run: it upgrades the code, dependencies, and unit,
but never overwrites an existing config. If you're already in a graphical
session it (re)starts the service immediately.

The unit is deliberately **not enabled**: wspr needs the session's
`DISPLAY`/`XAUTHORITY`, which don't exist until X starts, so systemd must not
launch it at boot. Instead, have your window manager start it once the session
is up. For i3:

```
exec --no-startup-id systemctl --user import-environment DISPLAY XAUTHORITY && systemctl --user restart wspr.service
```

The service restarts automatically on failure, and its output goes to the
journal:

```sh
systemctl --user status wspr    # is it running?
journalctl --user -u wspr -f    # follow logs
systemctl --user stop wspr      # stop it
```

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

## Command line

```
wspr                 # run the push-to-talk daemon (what the service does)
wspr COMMAND [...]   # anything else is forwarded to the command plugin
```

Core owns no subcommands; the vocabulary belongs to the configured plugin.
With wspr-i3 that is currently:

```sh
wspr exec switch to workspace three   # route one transcript and execute it
```

`exec` is handy for testing routing without speaking. `wspr -h` prints usage;
an unrecognized command prints the plugin's own usage.

## Configuration

wspr reads the first `wspr.toml` found in: `$WSPR_CONFIG`, the app directory,
`~/.config/wspr/wspr.toml`. Each `[[hotkeys]]` entry is one push-to-talk
binding. The shipped default:

```toml
[[hotkeys]]
combo = "super+space"
sink = "type"

[[hotkeys]]
combo = "super+shift+d"
sink = "command"

[command]
module = "wspr_i3"     # the plugin that handles command bindings

[model]
size = "base.en"       # tiny.en, base.en, small.en, medium, large-v3
device = "cpu"         # cpu, cuda
compute_type = "int8"  # int8 (CPU), float16 (GPU)
```

`[model]` is the speech-to-text stage, used by every binding. `[ollama]`
(url, model, timeout, keep_alive) tunes the routing stage and is only read by
command bindings. A socket binding takes an optional `socket` key to override
the default path (`$XDG_RUNTIME_DIR/wspr.sock`).

## Voice commands (the command sink)

A `command` binding hands the transcript to the plugin named under
`[command]`. wspr-i3 sends the text to a local Ollama model, which must answer
with constrained JSON naming a whitelisted action. The reply is validated and 
then executed with `i3-msg`. A desktop notification reports the outcome: 
executed, refused, no matching command, or routing failed.

The whitelist currently holds workspace switching ("switch to workspace
three", 1-10). More actions can and will be added.

The plugin seam is generic: core imports whatever module `[command]` names,
from the app directory or `~/.local/share/wspr/plugins`, so plugins for other
window managers can exist without any core changes. A plugin is a module
exposing `handle(text, cfg)` for transcripts and `cli(argv, cfg)` for forwarded 
subcommands.

## Testing the socket sink

`test_listener.py` is a minimal consumer for the **socket** sink. It binds the
same Unix socket (`$XDG_RUNTIME_DIR/wspr.sock`) that wspr connects to and prints
each transcript it receives, so you can confirm the sink works before wiring up
a real application. It uses only the standard library, so no venv is needed.

You need a hotkey routed to the socket sink; add one to your config with a
combo that isn't already bound:

```toml
[[hotkeys]]
combo = "super+shift+s"
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

Hold the socket hotkey, speak, and release. The transcript is sent over the
socket and printed by the listener:

```
  received: 'hello from the socket sink'
```

Ctrl-C stops the listener and removes the socket file.
