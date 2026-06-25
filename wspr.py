import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from Xlib import X, XK, display


SAMPLE_RATE = 16000     # faster-whisper expects 16 kHz
CHANNELS = 1            # mono

# Default path for the socket sink, setup by test_listener.py
DEFAULT_SOCKET = str(Path(os.environ["XDG_RUNTIME_DIR"]) / "wspr.sock")

# Valid sink values for a hotkey binding to check for when parsing config.
SINKS = ("type", "socket")

# Map of modifier name strings to the X bitmask constants they represent.
MODIFIER_MASKS: dict[str, int] = {
    "super": X.Mod4Mask,
    "ctrl":  X.ControlMask,
    "alt":   X.Mod1Mask,
    "shift": X.ShiftMask,
}

# Map of special key names to their X keysym constants.
NAMED_KEYSYMS: dict[str, int] = {
    "space": XK.XK_space,
    "enter": XK.XK_Return,
    "tab": XK.XK_Tab,
    "esc": XK.XK_Escape,
    "backspace": XK.XK_BackSpace,
}

# Lock modifiers to ignore when grabbing: CapsLock (LockMask) and NumLock
# (Mod2Mask). XGrabKey matches modifiers exactly, so we must register the
# combo for every on/off combination of these to catch all real-world states.
IGNORED_MOD_MASKS = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]


@dataclass
class Binding:
    """One push-to-talk hotkey: the original string plus the X grab values."""
    combo:       str                    # human-readable, e.g. "super+space"
    mask:        int                    # X modifier bitmask
    keycode:     int                    # X keycode for the trigger key
    sink:        str = "type"           # where to send: type | socket
    socket_path: str = DEFAULT_SOCKET   # destination when sink == "socket"


def trigger_keysym(name: str) -> int:
    """Map a trigger key name (space, tab, a, ...) to an X keysym integer."""
    if name in NAMED_KEYSYMS:
        return NAMED_KEYSYMS[name]
    if len(name) == 1:
        # ASCII printable chars: keysym == code point
        return ord(name)
    raise ValueError(f"Unknown trigger key {name!r}")


def parse_hotkey(combo: str, disp) -> Binding:
    """Turn a hotkey string (like 'super+space') into a Binding.

    The combo is '+'-separated: zero or more modifier names followed by a
    single trigger key. 

    Raises ValueError on an empty combo, unknown modifier, or a trigger that 
    has no keycode on this keyboard layout.
    """

    # Normalize the combo by splitting on +, dropping whitespace, trimming, 
    # and lowering
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey combo: {combo!r}")

    # Everything is a modifier except the last part, which is the trigger key
    *mods, trigger_name = parts

    # Build the modifier mask
    # Start at 0 and 'OR' in each modifier bit
    # With no modifiers, the mask stays 0
    mask = 0
    for m in mods:
        if m not in MODIFIER_MASKS:
            raise ValueError(f"Unknown modifier {m!r} in hotkey {combo!r}")
        mask |= MODIFIER_MASKS[m]

    # Turn the trigger_name (space) into a trigger keysym (layout-independent
    # representation of 'the space key') into a keycode (actual hardware num)
    keycode = disp.keysym_to_keycode(trigger_keysym(trigger_name))
    if keycode == 0:
        raise ValueError(f"Trigger {trigger_name!r} has no keycode on this layout")

    return Binding(combo, mask, keycode)


def grab_hotkey(root, keycode: int, mask: int) -> None:
    """ Ignore lock-modifiers when grabbing hotkeys."""
    for extra in IGNORED_MOD_MASKS:
        root.grab_key(keycode, mask | extra, False, X.GrabModeAsync, X.GrabModeAsync)


def ungrab_hotkey(root, keycode: int, mask: int) -> None:
    for extra in IGNORED_MOD_MASKS:
        root.ungrab_key(keycode, mask | extra)


class Recorder:
    """Captures microphone audio while the hotkey is held.

    We use callback mode in sounddevice because the recording duration is
    unknown and the main thread is already blocked in the X event loop waiting
    for the key release. In callback mode, PortAudio runs its own thread and
    invokes our callback every time a fresh block of samples is ready. Audio
    capture happens on that thread, leaving the main thread free.

    Because the callback runs on a real-time thread, it must not slow down. We
    copy the chunk to a thread-safe queue so PortAudio can reuse its buffer when
    the callback returns, keeping concatenation and transcription separate.
    """

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
        """Stop recording and return all captured audio as a 1-D float32 array."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        chunks = []
        while not self._frames.empty():
            chunks.append(self._frames.get_nowait())
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks, axis=0).reshape(-1)


def notify(body: str) -> None:
    """Show a desktop notification."""
    subprocess.run(["notify-send", "wspr", body], check=False)


def sink_type(text: str) -> None:
    """Type the transcript into the focused window via xdotool."""
    # --clearmodifiers releases any held modifier (e.g. Super still down from
    # the hotkey) so it doesn not corrupt the output.
    # The trailing space keeps consecutive dictations from running together.
    # check=False 
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text + " "])


def sink_socket(text: str, path: str) -> None:
    """Send the transcript over a Unix socket for another program to consume."""
    try:
        # One connection per transcript: connect, send, close.
        # AF_UNIX: Address family for interprocess comm on same machine.
        # SOCK_STREAM: Stream of text with connection to listener.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            # Open a connection to the application listening at 'path'
            c.connect(path)
            # Encode as UTF-8 bytes. Decode reverses on the receiving side.
            c.sendall(text.encode())
    except OSError as e:
        # Missing listener raises OSError. Report and swallow so it doesn't 
        # crash the loop.
        print(f"Socket sink failed: {e}", file=sys.stderr)
        notify("socket sink failed: is the listener running?")


def emit(text: str, binding: Binding) -> None:
    """Route a transcript to whichever sink its binding selects."""
    if binding.sink == "socket":
        sink_socket(text, binding.socket_path)
    else:
        sink_type(text)


def find_config_path() -> Path | None:
    """Return the config path to load, or None if there is none.

    Walks the priority cascade and returns the first existing config path, or 
    None. ($WSPR_CONFIG --> ./wspr.toml --> ~/.config/wspr/wspr.toml)
    """
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


def parse_bindings(cfg: dict, disp) -> list[Binding]:
    """Build the hotkey bindings from parsed config.

    Each [[hotkeys]] entry needs a 'combo' and may set 'sink' (type | socket,
    default type) and 'socket' (the path for the socket sink). Raises
    ValueError on a missing combo, an unknown sink, or a duplicate combo.
    """
    bindings: list[Binding] = []
    seen: set[tuple[int, int]] = set()
    for entry in cfg.get("hotkeys", []):
        combo = entry.get("combo")
        if not combo:
            raise ValueError("[[hotkeys]] entry is missing 'combo'")
        sink = entry.get("sink", "type")
        if sink not in SINKS:
            raise ValueError(f"Unknown sink {sink!r} for hotkey {combo!r} "
                             f"(expected one of {', '.join(SINKS)})")
        binding = parse_hotkey(combo, disp)
        binding.sink = sink
        binding.socket_path = str(entry.get("socket", DEFAULT_SOCKET))
        # X refuses a second grab of the same key+modifiers, so catch it here.
        if (binding.keycode, binding.mask) in seen:
            raise ValueError(f"Duplicate hotkey {combo!r}")
        seen.add((binding.keycode, binding.mask))
        bindings.append(binding)
    return bindings


def main() -> None:
    # xdotool is a system package (not pip-installable). Without it the type
    # sink silently does nothing, so warn early rather than fail invisibly.
    if shutil.which("xdotool") is None:
        print("WARNING: xdotool not found; transcripts cannot be typed.",
              file=sys.stderr)
    # notify-send (libnotify) is wspr's only visible feedback when headless.
    if shutil.which("notify-send") is None:
        print("WARNING: notify-send not found; desktop notifications disabled.",
              file=sys.stderr)

    # Open a connection to the root of the X server. XGrabKey registers to a
    # window, but we want to work in any window, so we use the root window.
    disp = display.Display()
    root = disp.screen().root

    # Load config to build the hotkey bindings
    config_path = find_config_path()
    if config_path is not None and config_path.exists():
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f)
        print(f"Loaded config from {config_path}")
    else:
        cfg = {}
        print("WARNING: no config file found; using default super+space -> type text.",
              file=sys.stderr)

    # Convert config to bindings
    bindings = parse_bindings(cfg, disp)
    if not bindings:
        # For no config or no hotkey definitions in config, use default
        bindings = [parse_hotkey("super+space", disp)]

    # Grabbing keys in X is weird!
    # X returns keygrabs asynchronously, so it may take a while, and cause issues
    # if the key is already taken by X (multiple attempts to find a key combo that
    # worked for me). Setting a flag for errors and syncing seems to fix it. If
    # the flag is set, the combo is already taken.
    # Grab one at a time, syncing after each, so a failure names the bad combo.
    grab_failed = {"hit": False}
    disp.set_error_handler(lambda err, req: grab_failed.__setitem__("hit", True))
    for b in bindings:
        grab_hotkey(root, b.keycode, b.mask)
        disp.sync()
        if grab_failed["hit"]:
            msg = f"Could not grab {b.combo}: already bound by another program."
            print(msg, file=sys.stderr)
            notify(msg)
            return
    disp.set_error_handler(None)

    # Get model settings from config
    model_cfg = cfg.get("model", {})
    model_size = model_cfg.get("size", "base.en")
    device = model_cfg.get("device", "cpu")
    compute_type = model_cfg.get("compute_type", "int8")

    print(f"Loading faster-whisper model '{model_size}' ({device}/{compute_type})...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    recorder = Recorder(SAMPLE_RATE, CHANNELS)

    print("Ready. Hold a hotkey to record, release to transcribe. Ctrl-C to quit.")
    for b in bindings:
        dest = f"socket {b.socket_path}" if b.sink == "socket" else b.sink
        print(f"  {b.combo}  ->  {dest}")
    notify("Ready: " + ", ".join(b.combo for b in bindings))

    # Holding down keys does not silence until you let go. X sends stram of fake 
    # release+press events. Naive loops see the first release and think 
    # recording should end. Detect fake releases with look-ahead buffer for a 
    # release followed immediately by a new, same-key press at the same timestamp.
    buffered = None

    def next_event():
        # Get the next event for autorepeat detection
        nonlocal buffered
        if buffered is not None:
            ev, buffered = buffered, None
            return ev
        return disp.next_event()

    def is_autorepeat(release_event) -> bool:
        # Peek ahead to see if the next event is a genuine release
        nonlocal buffered
        if buffered is None and disp.pending_events():
            buffered = disp.next_event()
        nxt = buffered
        if (nxt is not None # All 4 match for an autorepeat event
                and nxt.type == X.KeyPress 
                and nxt.detail == release_event.detail # the keycode on a key event
                and nxt.time == release_event.time):
            buffered = None     # consume the paired press; key is still held
            return True
        return False

    def match_binding(press_event) -> Binding | None:
        # A grab only fires for registered combos, so keycode + modifier state
        # (minus the lock modifiers) identifies which binding was pressed.
        state = press_event.state & ~(X.LockMask | X.Mod2Mask)
        for b in bindings:
            if press_event.detail == b.keycode and state == b.mask:
                return b
        return None

    def transcribe_and_emit(audio, duration, binding) -> None:
        # Runs on a background thread so transcription doesn't block the event
        # loop and the hotkey stays responsive for the next take.
        print(f"Transcribing {duration:.1f}s of audio...")
        segments, _ = model.transcribe(audio, language="en")
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if text:
            print(f"  -> {text!r}")
            emit(text, binding)
        else:
            print("  (no speech detected)")

    recording: Binding | None = None
    start_time = 0.0

    try:
        while True:
            event = next_event()

            # Skip non-key events (FocusIn/Out, MappingNotify, etc)
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue

            if event.type == X.KeyPress and recording is None:
                binding = match_binding(event)
                if binding is None:
                    # Some other key delivered during a grab
                    continue
                recording = binding
                start_time = time.monotonic()
                print(f"Recording ({binding.combo})...")
                recorder.start()

            elif event.type == X.KeyRelease and recording is not None:
                # Stop as soon as any key is released: the whole combo must stay
                # held to keep recording.
                if is_autorepeat(event):
                    # Detected fake release
                    continue
                binding, recording = recording, None
                duration = time.monotonic() - start_time
                print("Stopped.")
                audio = recorder.stop()

                # Transcribe off the event loop to free the hotkey.
                # daemon=True so an active transcription doesn't block Ctrl-C
                threading.Thread(
                    target=transcribe_and_emit,
                    args=(audio, duration, binding),
                    daemon=True,
                ).start()

    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        for b in bindings:
            ungrab_hotkey(root, b.keycode, b.mask)
        disp.flush()


if __name__ == "__main__":
    main()
