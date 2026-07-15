"""wspr-i3: voice-command plugin for wspr.

Routes a command transcript through a local LLM (Ollama) onto a whitelisted
i3 action, validates, and executes. Loaded by wspr through the plugin seam
(module = "wspr_i3" under [command] in wspr.toml). Absorbed from hark
(github.com/michaelgilch/hark).

Verbs:
    exec TEXT...    route one transcript and execute it
"""

import json
import subprocess
import sys
import threading
import urllib.request

# Any key the code reads from cfg["ollama"] must have a default here, so the
# command sink works with no [ollama] section at all; wspr.toml will only
# name what it overrides.
DEFAULTS = {
    "url": "http://localhost:11434",
    "model": "gemma3:1b",
    "timeout": 30,        # seconds to wait for a routing reply
    "keep_alive": "30m",  # how long Ollama keeps the model warm
}

# wspr transcribes each utterance on its own thread. hark's serve loop handled
# transcripts strictly one at a time; this lock preserves that, so two
# in-flight utterances can't land i3 commands out of order.
_lock = threading.Lock()


# --- Helpers ----------------------------------------------------------------

def i3msg(*cmds: str) -> None:
    """ Send one or more commands to i3 in a single i3-msg call """
    subprocess.run(["i3-msg", "; ".join(cmds)], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(summary: str, body: str = "") -> None:
    """ Desktop notification """
    subprocess.run(["notify-send", summary, body], check=False)


# --- Actions ---------------------------------------------------------------

def switch_workspace(n: int) -> str:
    i3msg(f"workspace number {n}")
    return f"workspace {n}"


# The whitelist: the only things the command sink can do.
# The LLM picks by name. validate() decides whether the pick may run.
ACTIONS = {
    "switch_workspace": switch_workspace,
}


# --- Routing ---------------------------------------------------------------

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


def validate(reply: dict) -> tuple[str, dict] | None:
    """ Only a whitelisted action with validated args gets out.
    Returns (name, args) to run, or None for a deliberate "none".
    Raises ValueError on anything malformed: refused, never coerced. """
    action = reply.get("action")
    if action == "none":
        return None
    if action == "switch_workspace":
        n = reply.get("n")
        if not isinstance(n, int):
            raise ValueError("switch_workspace without a workspace number")
        if not 1 <= n <= 10:
            raise ValueError(f"workspace {n} out of range 1-10")
        return "switch_workspace", {"n": n}
    raise ValueError(f"unknown action {action!r}")


def handle(text: str, cfg: dict) -> None:
    with _lock:
        print(f"heard: {text!r}")
        try:
            reply = route_llm(text, cfg)
        except Exception as e:
            print(f"  routing failed: {e}", file=sys.stderr)
            notify("wspr ▸ " + text, "routing failed (is Ollama up?)")
            return
        print(f"  llm: {reply}")
        try:
            routed = validate(reply)
        except ValueError as e:
            print(f"  refused: {e}")
            notify("wspr ▸ " + text, f"refused: {e}")
            return
        if routed is None:
            print("  no matching command")
            notify("wspr ▸ " + text, "no matching command")
            return
        name, args = routed
        try:
            result = ACTIONS[name](**args)
        except Exception as e:
            print(f"  action failed: {e}", file=sys.stderr)
            notify("wspr", f"{name} failed: {e}")
            return
        print(f"  done: {result}")
        notify("wspr ▸ " + text, result)


# --- wspr CLI surface -------------------------------------------------------

def cli(argv: list[str], cfg: dict) -> int:
    """ Dispatch a wspr subcommand (argv is sys.argv[1:] from the wspr CLI).
    Returns a process exit code. """
    cmd, *rest = argv
    text = " ".join(rest).strip()
    if cmd == "exec" and text:
        handle(text, cfg)
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 1
