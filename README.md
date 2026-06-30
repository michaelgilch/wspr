# wspr

**wspr** is a push-to-talk voice dictation and transcription tool for Linux/X11. 

You hold a hotkey (default Super+Space), speak, and release. The audio is transcribed locally with faster-whisper and routed to the hotkeys configured **sink**.

The default sink uses `xdotool` to type the transcribed text into the focused window (classic dictation behavior).

A second sink, with its own hotkey, opens a Unix socket to a listener. This socket can be used to send the transcription to another application such as a note taker, prompt-feeder, or home automation script.

## Why not use existing software?

For plain dictation, I probably wouldn't. Plenty of tools already exist. However, I couldn't find anything that matched my exact needs:

- **Model choice**. I wanted fully local and accurate. **faster-whisper** is noticably better than some of the alternatives packaged with existing tools.
- **Socket sink**. I could not find a tool that gave me steaming over a Unix socket to _any_ listener. I wanted composability to not re-invent the wheel each time I have an idea.

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

Install the Python dependencies into a virtual environment:

```sh
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

