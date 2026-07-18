"""Routing for wspr-i3: transcript text -> validated whitelist action.
"""

import json
import urllib.request
from dataclasses import dataclass, field

# Any key the code reads from cfg["ollama"] must have a default here, so the
# command sink works with no [ollama] section at all; wspr.toml will only
# name what it overrides.
DEFAULTS = {
    "url": "http://localhost:11434",
    "model": "gemma3:1b",
    "timeout": 30,        # seconds to wait for a routing reply
    "keep_alive": "30m",  # how long Ollama keeps the model warm
}


@dataclass
class Intent:
    """ The currency between routing and execution: everything that needs to
    reason about "a command about to run" (the confirm gate, dry runs,
    logging) works on this one object. """
    name: str
    args: dict = field(default_factory=dict)
    confidence: float = 1.0     # the model's own certainty, clamped to [0,1]
    heard: str = ""

    def describe(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.name}({args})"


SYSTEM = """\
You convert voice-command transcripts into JSON for an i3 window manager \
controller. The only available action is switch_workspace, which takes n, \
a workspace number from 1 to 10.

If the transcript asks to switch, go to, or move to a workspace, reply with \
{"action": "switch_workspace", "n": <number>, "confidence": <0.0-1.0>}. For \
anything else, including other desktop commands and workspace numbers \
outside 1-10, reply with {"action": "none", "confidence": <0.0-1.0>}.
confidence is how sure you are that this is what the user meant.

Examples:
"Switch to workspace two." -> {"action": "switch_workspace", "n": 2, "confidence": 0.98}
"go to the ninth workspace" -> {"action": "switch_workspace", "n": 9, "confidence": 0.95}
"lock the screen" -> {"action": "none", "confidence": 0.9}
"open a terminal" -> {"action": "none", "confidence": 0.9}
"switch to workspace fifty" -> {"action": "none", "confidence": 0.85}
"workspace" -> {"action": "none", "confidence": 0.6}
"close workspace two" -> {"action": "none", "confidence": 0.7}

Transcripts come from speech recognition and may contain extra punctuation, \
capitalization, or filler words."""

# Constrained decode. The model can only emit tokens that fit this schema.
# One branch per action shape: a flat schema can't make "n" required for
# switch_workspace but absent for none, and with "n" merely optional the
# model happily omits it ("go to workspace four" -> no n at all).
# Structure only, no min/max on n: a range constraint would truncate an
# out-of-range answer into a valid-looking one ("eleven" -> 1). The 1-10
# range is validate()'s job, where a violation is refused, not mangled.
SCHEMA = {
    "oneOf": [
        {"type": "object",
         "properties": {"action": {"const": "switch_workspace"},
                        "n": {"type": "integer"},
                        "confidence": {"type": "number"}},
         "required": ["action", "n", "confidence"]},
        {"type": "object",
         "properties": {"action": {"const": "none"},
                        "confidence": {"type": "number"}},
         "required": ["action", "confidence"]},
    ],
}


def route_llm(text: str, cfg: dict) -> dict:
    """ Ask the local model to map a transcript onto the action vocabulary.
    Returns its parsed JSON reply. Raises on transport or parse failure. """
    o = {**DEFAULTS, **cfg.get("ollama", {})}
    payload = json.dumps({
        "model": o["model"],
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0},   # routing wants determinism
        "keep_alive": o["keep_alive"],
    }).encode()
    req = urllib.request.Request(o["url"] + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=o["timeout"]) as resp:
        reply = json.load(resp)
    return json.loads(reply["message"]["content"])


def clamp_confidence(value: object) -> float:
    """ The model's confidence is a claim, not a fact; keep it in [0,1] and
    treat garbage as 'unsure' rather than an error. """
    try:
        return max(0.0, min(1.0, float(value)))     # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5


def validate(reply: dict) -> Intent | None:
    """ The trust boundary: only a whitelisted action with validated args
    becomes an Intent. Returns None for a deliberate "none". Raises
    ValueError on anything malformed: refused, never coerced. """
    action = reply.get("action")
    if action == "none":
        return None
    confidence = clamp_confidence(reply.get("confidence", 0.5))
    if action == "switch_workspace":
        n = reply.get("n")
        if not isinstance(n, int):
            raise ValueError("switch_workspace without a workspace number")
        if not 1 <= n <= 10:
            raise ValueError(f"workspace {n} out of range 1-10")
        return Intent("switch_workspace", {"n": n}, confidence=confidence)
    raise ValueError(f"unknown action {action!r}")
