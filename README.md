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
- **For the command sink** (the wspr-i3 plugin, installed via `./install.sh --with-i3`; dictation works without these):
  - **i3** - actions execute through `i3-msg` (or optionally the `python-i3ipc`
    package, see `[i3]` in the config)
  - **Ollama** running locally with the configured routing model (default
    `gemma4:latest`)
  - **rofi** - the confirm gate, the window picker, and the `wspr prompt`
    text box

`xdotool` and `notify-send` are not pip-installable; install them from your distro's package manager. wspr warns at startup if either is missing rather than failing silently, and `--with-i3` warns about missing rofi, `i3-msg`, or an unreachable Ollama.

## Install

The recommended way to install wspr for everyday use is `install.sh`:

```sh
./install.sh              # dictation-only core
./install.sh --with-i3    # core + the wspr-i3 voice-command plugin
./install.sh --remove-i3  # drop the plugin, keep the core
./install.sh --uninstall  # remove everything except your config
```

It sets everything up under your home directory (no root needed):

| What                         | Where                                       |
|:-----------------------------|:--------------------------------------------|
| App + private venv           | `~/.local/share/wspr/`                      |
| wspr-i3 plugin (`--with-i3`) | `~/.local/share/wspr/wspr_i3/`              |
| Launcher executable          | `~/.local/bin/wspr`                         |
| Default config               | `~/.config/wspr/wspr.toml` (only if absent) |
| systemd user service         | `~/.config/systemd/user/wspr.service`       |

It creates the venv, installs the dependencies from `requirements.txt`, and (if
`nvidia-smi` is present) adds the CUDA cuBLAS/cuDNN wheels for `device = "cuda"`.
The installer is safe to re-run: it upgrades the code, dependencies, and
service, and refreshes an installed plugin so core and plugin never skew. It
never overwrites an existing config. The plugin is sticky: only an explicit
`--remove-i3` removes it, and `--uninstall` keeps your config for the next
install. If you're already in a graphical session it (re)starts the service
immediately.

The service is deliberately **not enabled**: wspr needs the session's
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
wspr prompt                            # rofi text box: type a command instead of speaking it
wspr route switch to workspace three   # dry run: print the routed action, execute nothing
wspr exec switch to workspace three    # route one transcript and execute it
wspr context                           # show what wspr-i3 knows about this machine
wspr windows                           # list the windows wspr-i3 sees (debug)
```

`route` shows what the router decided - source (fast/llm), confidence, and
whether it would confirm - without side effects; `exec` is the same pipeline
and actually executes. `prompt` is the no-microphone entry: a rofi text box
whose contents go through the same routing, gates, and whitelist as speech.
Bind it to a key (e.g. `bindsym $mod+Ctrl+d exec ~/.local/bin/wspr prompt`).
It runs in its own process, outside the daemon's routing lock - fine for one
human at one keyboard. `wspr -h` prints usage; an unrecognized command prints
the plugin's own usage.

## Configuration

wspr reads the first `wspr.toml` found in: `$WSPR_CONFIG`, the app directory,
`~/.config/wspr/wspr.toml`. Each `[[hotkeys]]` entry is one push-to-talk
binding. The shipped default is dictation-only:

```toml
[[hotkeys]]
combo = "super+space"
sink = "type"

[model]
size = "base.en"       # tiny.en, base.en, small.en, medium, large-v3
device = "cpu"         # cpu, cuda
compute_type = "int8"  # int8 (CPU), float16 (GPU)
```

To enable voice commands, install the plugin (`./install.sh --with-i3`) and
uncomment the command binding and `[command]` section that the shipped config
carries:

```toml
[[hotkeys]]
combo = "super+ctrl+space"
sink = "command"

[command]
module = "wspr_i3"     # the plugin that handles command bindings
```

`[model]` is the speech-to-text stage, used by every binding. A socket binding
takes an optional `socket` key to override the default path
(`$XDG_RUNTIME_DIR/wspr.sock`). Two optional keys tune transcription per
binding: `vocab_bias` (default true) primes whisper with the plugin's command
vocabulary on command bindings, so command words ("workspace", app names)
transcribe reliably; `initial_prompt` replaces that with literal bias text and
works on any binding.

The remaining sections are read only by the plugin:

- `[ollama]` - the routing stage: `url`, `model`, `timeout`, `keep_alive`,
  and `confidence_threshold` (below it, `uncertain` mode asks first).
  Dictation never touches Ollama.
- `[confirm]` - when a routed command asks (rofi yes/no) before executing:
  `always` every command, `uncertain` only privileged actions and shaky
  routings (the default), `never` only privileged actions.
- `[i3]` - how the plugin talks to i3: `i3msg` shells out to `i3-msg`
  (the default, zero dependencies); `i3ipc` uses the `python-i3ipc` package
  and fails loud at startup if it isn't installed.

### Choosing the routing model

Measured on the repo's `experiments/eval.py` battery, 36 cases (2026-07-19):

| Model           | Score | Warm latency | Footprint |
|:----------------|:------|:-------------|:----------|
| `gemma4:latest` | 36/36 | ~1.0 s mean  | 9.6 GB VRAM held warm |
| `gemma3:4b`     | 32/36 | ~500 ms mean | 3.3 GB |
| `gemma3:1b`     | 24/29 (older battery) | ~490 ms | 815 MB |

The smaller models' misses are **wrong actions**, not refusals: "move to
workspace five" moves the window instead of switching, and questions
hallucinate actions. The move-vs-switch object-word distinction and
none-discipline on questions are what demand the default `gemma4:latest`;
pick a smaller model only if you accept occasional wrong actions.

## Voice commands (the command sink)

A `command` binding hands the transcript to the plugin named under
`[command]`. In wspr-i3 the transcript goes through this pipeline:

1. **Fast path.** Two anchored regex rules route the highest-frequency
   commands - workspace switches ("workspace three", "go to workspace 5")
   and curated app launches ("open kitty") - instantly, offline, and
   deterministically. Anything with extra words falls through to the model.
2. **LLM routing.** A local Ollama model gets a system prompt grounded in
   *this machine* - its hostname, declared packages, i3 workspace names, and
   curated app aliases - and must answer with constrained JSON naming a
   whitelisted action. Descriptive requests work ("open my photo editor"
   resolves to gimp if installed); apps that aren't installed are refused.
3. **Validation.** Only a whitelisted action with validated args becomes
   executable. App names are re-resolved locally against the machine's own
   package list - the command that runs is never taken from the model.
4. **Confirm gate.** Privileged actions (lock screen, system updates) always
   ask first; in `uncertain` mode, shaky routings (fuzzy app resolution, low
   confidence) ask too. The rofi yes/no lists No first, so a reflexive Enter
   cancels.
5. **Execution**, with a desktop notification reporting the outcome:
   executed, refused, no matching command, or routing failed.

The whitelist currently holds: switch workspace, move the focused window to a
workspace, move a *named* window to a workspace ("put chrome on workspace
three"), focus a window, launch an app (optionally on a workspace), lock the
screen, and run system updates. Window queries match against class and title
with tiered narrowing; if several windows still match, a rofi picker asks
which one you meant.

If Ollama is down, the fast-path vocabulary keeps working and fuzzier
phrasings fail with a clear "routing failed" notification - the daemon
degrades to a useful core instead of dying.

### Plugins

The plugin seam is generic: core imports whatever module `[command]` names,
from the app directory or `~/.local/share/wspr/plugins`, so plugins for other
window managers can exist without any core changes. A plugin is a module
exposing up to four functions; only `handle` is required:

- `handle(text, cfg)` - route and execute one transcript
- `prepare(cfg)` - startup init, called once before any keys are grabbed;
  fail loud here, not on the first utterance
- `vocabulary() -> str` - short bias text for whisper's `initial_prompt` on
  command bindings (see `vocab_bias` above)
- `cli(argv, cfg) -> int` - the forwarded subcommands

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
