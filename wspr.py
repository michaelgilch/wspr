import queue
import sys

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000     # faster-whisper expects 16 kHz
CHANNELS = 1            # mono

class Recorder:
    """Captures microphone audio between start() and stop().

    We use callback mode in sounddevice because the recording duration is 
    unknown and the main thread is already blocked in input() waiting for the 
    STOP keypress. In callback mode, PortAudio runs its own thread and invokes
    our callback every time a fresh block of samples is ready. Audio capture 
    happens on that thread, leaving the main thread free. 

    Because the callback runs on a real-time thread, it must not slow down. We 
    copy the chunk to a thread-safe queue so PortAudio can reuse its buffer when
    the callback returns, keeping concatentation and transcription separate. 
    """

    def __init__(self, sample_rate, channels):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames = queue.Queue() # for thread-safe handoff of audio chunks
        self._stream = None

    def _callback(self, input_data, frames, time_info, status):
        # Called by PortAudio repeatedly, passing new audio to our chunk queue.
        # Prints any errors that exist in status to stderr (informational).
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        self._frames.put(input_data.copy())

    def start(self):
        # Flush any leftover chunks from the previous recording
        while not self._frames.empty():
            self._frames.get_nowait()
        # Create the input stream with our settings and register our callback.
        self._stream = sd.InputStream(
            samplerate = self.sample_rate,
            channels = self.channels,
            dtype = "float32",
            callback = self._callback,
            )
        # Start the stream.
        self._stream.start()

    def stop(self):
        # Close the stream.
        self._stream.stop()
        self._stream.close()
        self._stream = None
        # Move the queue of chunks into a list
        chunks = []
        while not self._frames.empty():
            chunks.append(self._frames.get_nowait())
        # Return an empty numpy array if the list is empty, so the transcription
        # has something valid to work with.
        if not chunks:
            return np.empty(0, dtype=np.float32)
        # Otherwise return a 1D numpy array of all the chunks
        return np.concatenate(chunks, axis=0).reshape(-1)  # flatten to 1-D mono

model = WhisperModel("base.en", device="cpu", compute_type="int8")
recorder = Recorder(SAMPLE_RATE, CHANNELS)

print("Press Enter to START recording. Ctrl+C to quit.")
try:
    while True:
        input()         # wait for Enter to start
        recorder.start()
        input(" recording... press Enter to STOP")  # wait for Enter to stop
        audio = recorder.stop()
        # Pass the audio to the WhisperModel for transcription. Returns an
        # iterable of transcribed chunks and metadata (which is discarded).
        segments, _ = model.transcribe(audio, language="en")
        text = " ".join(seg.text.strip() for seg in segments).strip()
        print(f" -> {text}\n")
except KeyboardInterrupt:
    print("\nBye")