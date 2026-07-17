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
    heard: str = ""

    def describe(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.name}({args})"


SYSTEM = """\
You convert voice-command transcripts into JSON for an i3 window manager \
controller. The only available action is switch_workspace, which takes n, \
a workspace number from 1 to 10.

If the transcript asks to switch, go to, or move to a workspace, reply with \
{"action": "switch_workspace", "n": <number>}. For anything else, including \
other desktop commands and workspace numbers outside 1-10, reply with \
{"action": "none"}.

Examples:
"Switch to workspace two." -> {"action": "switch_workspace", "n": 2}
"go to the ninth workspace" -> {"action": "switch_workspace", "n": 9}
"lock the screen" -> {"action": "none"}
"open a terminal" -> {"action": "none"}
"switch to workspace fifty" -> {"action": "none"}
"workspace" -> {"action": "none"}
"close workspace two" -> {"action": "none"}

Transcripts come from speech recognition and may contain extra punctuation, \
capitalization, or filler words."""

# Constrained decode. The model can only emit tokens that fit this schema.
# Structure only, no min/max on n: a range constraint would truncate an
# out-of-range answer into a valid-looking one ("eleven" -> 1). The 1-10
# range is validate()'s job, where a violation is refused, not mangled.
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["switch_workspace", "none"]},
        "n": {"type": "integer"},
    },
    "required": ["action"],
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


def validate(reply: dict) -> Intent | None:
    """ The trust boundary: only a whitelisted action with validated args
    becomes an Intent. Returns None for a deliberate "none". Raises
    ValueError on anything malformed: refused, never coerced. """
    action = reply.get("action")
    if action == "none":
        return None
    if action == "switch_workspace":
        n = reply.get("n")
        if not isinstance(n, int):
            raise ValueError("switch_workspace without a workspace number")
        if not 1 <= n <= 10:
            raise ValueError(f"workspace {n} out of range 1-10")
        return Intent("switch_workspace", {"n": n})
    raise ValueError(f"unknown action {action!r}")
