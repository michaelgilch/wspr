import sounddevice as sd
from faster_whisper import WhisperModel

model = WhisperModel("base.en", device="cpu", compute_type="int8")
input("Press Enter, then speak for 5 seconds...")
audio = sd.rec(int(5 * 16000), samplerate=16000, channels=1, dtype="float32")
sd.wait()
segments, _ = model.transcribe(audio.reshape(-1), language="en")
print(" ".join(seg.text for seg in segments))