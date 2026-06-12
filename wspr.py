#!/usr/bin/env python3
"""
wspr - push-to-talk voice dictation

Hold the configured hotkey (default Super+F1) to record, release to transcribe
with faster-whisper, and the text is typed into whatever window has focus.

Designed for X11. The hotkey is grabbed via the X server (XGrabKey) so the
keypress goes only to wspr and does not leak into the focused window. Text is
injected with xdotool.

Run inside the project venv:
    ./.venv/bin/python wspr.py
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from Xlib import X, XK, display


MODIFIER_X_MASKS: dict[str, int] = {
    "super": X.Mod4Mask,
    "ctrl": X.ControlMask,
    "alt": X.Mod1Mask,
    "shift": X.ShiftMask,
}

NAMED_KEYSYMS: dict[str, int] = {
    "space": XK.XK_space,
    "enter": XK.XK_Return,
    "tab": XK.XK_Tab,
    "esc": XK.XK_Escape,
    "backspace": XK.XK_BackSpace,
}

# Lock modifiers that must be ignored: a grab matches modifiers exactly, so we
# register the combo for every on/off state of CapsLock (Lock) and NumLock (Mod2).
IGNORED_MOD_MASKS = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]


def trigger_keysym(name: str) -> int:
    """Map a trigger key name (f1, space, `, p, ...) to an X keysym."""
    if len(name) > 1 and name[0] == "f" and name[1:].isdigit():
        return getattr(XK, f"XK_F{name[1:]}")        # f1..f20
    if name in NAMED_KEYSYMS:
        return NAMED_KEYSYMS[name]
    if len(name) == 1:
        # For ASCII/Latin-1 printable chars the keysym equals the code point
        # (e.g. '`' -> 0x60 == XK_grave, 'a' -> 0x61 == XK_a).
        return ord(name)
    raise ValueError(f"unknown trigger key {name!r}")


def parse_hotkey(combo: str, disp) -> tuple[int, int, str]:
    """ Turn a hotkey string into the pieces XGrabKey needs.

    The combo is "+"-separated: zero or more modifiers followed by a single
    trigger key, e.g. "ctrl+alt+f9" or just "f9". Returns a 3-tuple:

      modifier_mask: the OR of the X masks for the modifiers (0 if none).
      keycode:       the X keycode of the trigger key, for this keyboard layout.
      combo:         the original combo string, returned as-is for display.

    Raises ValueError on an empty combo or an unknown modifier/trigger name.
    """
    # Normalize combo by splitting on +, dropping empty/whitespace, trimming, and lowering
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey combo: {combo!r}")

    # Everything is a modifier except the last part, which is the trigger key
    *mods, trigger_name = parts

    # Build the modifier mask
    # Start at 0 and OR in each modifier bit
    # With no modifiers, mask stays 0
    mask = 0
    for m in mods:
        if m not in MODIFIER_X_MASKS:
            raise ValueError(f"Unknown modifier {m!r} in hotkey {combo!r}")
        mask |= MODIFIER_X_MASKS[m]

    # Turn the trigger into a keycode
    keycode = disp.keysym_to_keycode(trigger_keysym(trigger_name))
    if keycode == 0:
        raise ValueError(f"Trigger {trigger_name!r} has no keycode on this layout")

    return mask, keycode, combo


def find_config_path() -> Path | None:
    """ Walks the priority cascade and returns the first existing config path, or None.
    	($WSPR_CONFIG --> ./wspr.toml --> ~/.config/wspr/wspr.toml)"""
    env = os.environ.get("WSPR_CONFIG")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent / "wspr.toml"
    if here.exists():
        return here
    xdg = Path.home() / ".config" / "wspr" / "wspr.toml"
    if xdg.exists():
        return xdg
    return None


class Recorder:
    """ Captures microphone audio while the hotkey is held."""

    def __init__(self, sample_rate: int, channels: int):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        self._frames.put(indata.copy())

    def start(self) -> None:
        # Drain any leftover frames from a previous take.
        while not self._frames.empty():
            self._frames.get_nowait()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured audio as a 1-D float32 array."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        chunks = []
        while not self._frames.empty():
            chunks.append(self._frames.get_nowait())
        if not chunks:
            return np.empty(0, dtype=np.float32)
        audio = np.concatenate(chunks, axis=0)
        return audio.reshape(-1)  # flatten to mono


def type_text(text: str) -> None:
    """ Inject text into the focused window via xdotool."""
    text = text.strip()
    if not text:
        return
    # --clearmodifiers avoids a held modifier messing up the output
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text], check=False)


def grab_hotkey(root, keycode: int, mask: int) -> None:
    """ Ignore lock-modifiers when grabbing hotkeys."""
    for extra in IGNORED_MOD_MASKS:
        root.grab_key(keycode, mask | extra, False, X.GrabModeAsync, X.GrabModeAsync)


def ungrab_hotkey(root, keycode: int, mask: int) -> None:
    for extra in IGNORED_MOD_MASKS:
        root.ungrab_key(keycode, mask | extra)


def main() -> None:
	# Determine correct config path
	# $WSPR_CONFIG -> ./wspr.toml -> ~/.config/wspr/wspr.toml
    config_path = find_config_path()
    if config_path is not None and config_path.exists():
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f)
        print(f"Loaded config from {config_path}")
    else:
        cfg = {}
        print("No config file found; using built-in defaults.")

    model_cfg = cfg.get("model", {})
    model_size = model_cfg.get("size", "base.en")
    device = model_cfg.get("device", "cpu")
    compute_type = model_cfg.get("compute_type", "int8")
    combo = cfg.get("hotkey", {}).get("combo", "super+space")

    # Open a connection to X server ($DISPLAY)
    disp = display.Display()

    # Get the root, top-level, window of the display (form multi-monitor X)
    # Root window allows hotkey to work globally and not application specific
    root = disp.screen().root

    # Convert the hotkey config string into a usable hotkey
    # mask = modifier bitmask (Mod4Mask) for Super = 0x40
    # keycode = hardware key number for the trigger key
    # hotkey_label = the combo string as configured, e.g. "super+f1"
    mask, keycode, hotkey_label = parse_hotkey(combo, disp)

    print(f"Loading faster-whisper model '{model_size}' ({device}/{compute_type})...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    # faster-whisper was trained on 16 kHz mono audio.
    recorder = Recorder(16000, 1)

    def transcribe_and_emit(audio: np.ndarray, duration: float) -> None:
        print(f"  transcribing {duration:.1f}s of audio...")
        segments, _ = model.transcribe(
            audio,
            language="en",
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if text:
            print(f"  → {text!r}")
            type_text(text + " ")
        else:
            print("  (no speech detected)")

    # Grabbing keys in X is weird!
    # X returns keygrabs asynchronously, so it may take a while, and cause issues
    # if the key is already taken by X (multiple attempts to find a key combo that
    # worked for me). Setting a flag for errors and syncing seems to fix it. If 
    # the flag is set, the combo is already taken.
    grab_failed = {"hit": False}
    disp.set_error_handler(lambda err, req: grab_failed.__setitem__("hit", True))
    grab_hotkey(root, keycode, mask)
    disp.sync()
    disp.set_error_handler(None)
    if grab_failed["hit"]:
        print(f"Could not grab {hotkey_label}: it's already bound.", file=sys.stderr)
        return

    print(f"Ready. Hold {hotkey_label} to dictate, release to transcribe. Ctrl-C to quit.")

    # Holding down keys does not silence until you let go. X sends stram of fake 
    # release+press events. Naive loops see the first release and think recording
    # should end. Detect fake releases with look-ahead buffer for a release followed
    # immediately by a new, same-key press at the same timestamp.
    recording = False
    start_time = 0.0
    buffered = None

    def next_event():
    	# Get the next event
        nonlocal buffered
        if buffered is not None:
            ev, buffered = buffered, None
            return ev
        return disp.next_event()

    def is_autorepeat(release_ev) -> bool:
    	# Peak ahead to see if the next event is a genuine release
        nonlocal buffered
        if buffered is None and disp.pending_events():
            buffered = disp.next_event()
        nxt = buffered
        if (nxt is not None and nxt.type == X.KeyPress
                and nxt.detail == keycode and nxt.time == release_ev.time):
            buffered = None  # consume the paired press; the key is still held
            return True
        return False

    try:
        while True:
            event = next_event()
            
            # Skip non-key events (FocusIn/Out, MappingNotify, etc)
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue
            
            # Skip other keys delivered during the current grab
            if event.detail != keycode:
                continue

            if event.type == X.KeyPress and not recording:
                recording = True
                start_time = time.monotonic()
                print(f"Recording...")
                recorder.start()
            elif event.type == X.KeyRelease and recording:
            	# Detect fake releases
                if is_autorepeat(event):
                    continue  # Fake release detected
                recording = False
                duration = time.monotonic() - start_time
                print("Stopped.")
                audio = recorder.stop()

                # Transcribe in a separate thread to keep the hotkey available
                threading.Thread(
                    target=transcribe_and_emit,
                    args=(audio, duration),
                    daemon=True,
                ).start()

    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        ungrab_hotkey(root, keycode, mask)
        disp.flush()


if __name__ == "__main__":
    main()
