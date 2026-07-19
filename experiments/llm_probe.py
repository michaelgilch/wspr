#!/usr/bin/env python3
"""Test local model to route workspace commands.
Prototype of wspr-i3's route_llm() using gemma3:1b."""

import json
import time
import urllib.request

URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:1b"

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
# range is the validator's job, in code, where a violation can be refused.
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["switch_workspace", "none"]},
        "n": {"type": "integer"},
    },
    "required": ["action"],
}


def ask(text: str) -> tuple[dict, float]:
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0},
        "keep_alive": "30m",
    }).encode()
    req = urllib.request.Request(URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as resp:
        reply = json.load(resp)
    elapsed = time.perf_counter() - start
    return json.loads(reply["message"]["content"]), elapsed


PHRASES = [
    "Switch to workspace two.",          # Whisper prose (caps + period)
    "go to the fifth workspace",         # ordinal word
    "workspace 3",                       # bare digit
    "Please move to workspace ten.",     # polite filler
    "jump over to workspace nine",       # weird phrasing 
    "make me a sandwich",                # expect "none"
    "lock the screen",                   # real command, but not in vocab yet
    "switch to workspace eleven",        # out of range: "none", or 11 for the
                                         # validator to refuse; never 1
    "workspace",                         # no number at all: expect "none"
    "close workspace two",               # mentions a workspace but isn't a switch
]

if __name__ == "__main__":
    for phrase in PHRASES:
        result, secs = ask(phrase)
        print(f"{secs*1000:7.0f} ms  {phrase!r:40} -> {result}")
