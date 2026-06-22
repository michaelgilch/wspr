import queue
import sys
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from Xlib import X, XK, display


SAMPLE_RATE = 16000     # faster-whisper expects 16 kHz
CHANNELS = 1            # mono

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
    combo:   str    # human-readable, e.g. "super+space"
    mask:    int    # X modifier bitmask
    keycode: int    # X keycode for the trigger key


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


def main() -> None:
    # Open a connection to the root of the X server. XGrabKey registers to a 
    # window, but we want to work in any window, so we use the root window.
    disp = display.Display()
    root = disp.screen().root

    # Convert the hotkey string to a Binding (modifier bitmask and keycode)
    binding = parse_hotkey("super+space", disp)

    # Grabbing keys in X is weird!
    # X returns keygrabs asynchronously, so it may take a while, and cause issues
    # if the key is already taken by X (multiple attempts to find a key combo that
    # worked for me). Setting a flag for errors and syncing seems to fix it. If 
    # the flag is set, the combo is already taken.
    grab_failed = {"hit": False}
    disp.set_error_handler(lambda err, req: grab_failed.__setitem__("hit", True))
    # Send the grab request to X
    grab_hotkey(root, binding.keycode, binding.mask)
    # Force flushing of all pending requests (due to async returns) to ensure we
    # have a reliably set grab_failed flag
    disp.sync()
    if grab_failed["hit"]:
        print(f"Could not grab {binding.combo}: already bound by another program.",
              file=sys.stderr)
        return
    disp.set_error_handler(None)

    print("Loading faster-whisper model...")
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    recorder = Recorder(SAMPLE_RATE, CHANNELS)

    print(f"Ready. Hold {binding.combo} to record, release to transcribe. Ctrl-C to quit.")

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

    def is_autorepeat(release_ev) -> bool:
        # Peek ahead to see if the next event is a genuine release
        nonlocal buffered
        if buffered is None and disp.pending_events():
            buffered = disp.next_event()
        nxt = buffered
        if (nxt is not None # All 4 match for an autorepeat event
                and nxt.type == X.KeyPress 
                and nxt.detail == release_ev.detail # the keycode on a key event
                and nxt.time == release_ev.time):
            buffered = None     # consume the paired press; key is still held
            return True
        return False

    recording = False
    start_time = 0.0

    try:
        while True:
            event = next_event()

            # Skip non-key events (FocusIn/Out, MappingNotify, etc)
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue

            if event.type == X.KeyPress and not recording:
                recording = True
                start_time = time.monotonic()
                print(f"Recording ({binding.combo})...")
                recorder.start()

            elif event.type == X.KeyRelease and recording:
                if is_autorepeat(event):
                    continue
                recording = False
                duration = time.monotonic() - start_time
                print("Stopped.")
                audio = recorder.stop()
                print(f"Transcribing {duration:.1f}s of audio...")
                segments, _ = model.transcribe(audio, language="en")
                text = " ".join(seg.text.strip() for seg in segments).strip()
                if text:
                    print(f"  -> {text!r}")
                else:
                    print("  (no speech detected)")

    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        ungrab_hotkey(root, binding.keycode, binding.mask)
        disp.flush()


if __name__ == "__main__":
    main()
