"""Routing for wspr-i3: transcript text -> validated whitelist action.
"""

import json
import urllib.request
from dataclasses import dataclass, field

from . import actions
from .context import Context

# Any key the code reads from cfg["ollama"] must have a default here, so the
# command sink works with no [ollama] section at all; wspr.toml will only
# name what it overrides.
DEFAULTS = {
    "url": "http://localhost:11434",
    "model": "gemma3:1b",
    "timeout": 30,        # seconds to wait for a routing reply
    "keep_alive": "30m",  # how long Ollama keeps the model warm
    "confidence_threshold": 0.7,  # below this, 'uncertain' mode asks first
}


@dataclass
class Intent:
    """ The currency between routing and execution: everything that needs to
    reason about "a command about to run" (the confirm gate, dry runs,
    logging) works on this one object. """
    name: str
    args: dict = field(default_factory=dict)
    privileged: bool = False    # confirms in every mode
    confidence: float = 1.0     # the model's own certainty, clamped to [0,1]
    uncertain: bool = False     # fuzzy resolution -> confirm in 'uncertain' mode
    heard: str = ""

    def describe(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.name}({args})"


def render_action_specs() -> str:
    """ One prompt line per SPECS entry, so the model's menu and the
    whitelist cannot drift apart. """
    lines = []
    for name, spec in actions.SPECS.items():
        if spec["args"]:
            args = ", ".join(f'"{a}": {m["desc"]}'
                             for a, m in spec["args"].items())
            lines.append(f"  {name}: args {{{args}}} - {spec['desc']}")
        else:
            lines.append(f"  {name}: no args - {spec['desc']}")
    return "\n".join(lines)


def render_system_prompt(ctx: Context) -> str:
    """ The model's briefing, grounded in this machine: its workspaces, its
    curated aliases, its declared packages. What lets a small local model
    act like it knows your computer: it chooses from your vocabulary. """
    ws = ", ".join(f'{n}: "{name}"' for n, name in sorted(ctx.workspaces.items()))
    aliases = ", ".join(f"{a} -> {c}" for a, c in sorted(ctx.launch_map.items()))
    pkgs = ", ".join(sorted(ctx.packages))
    return f"""\
You convert voice-command transcripts into JSON actions for this specific \
machine.

Machine: {ctx.host}, Arch Linux, i3 window manager.
i3 workspaces: {ws}
Application aliases: {aliases}
Installed packages: {pkgs}

Actions:
{render_action_specs()}

Rules:
- Reply with ONLY one JSON object: {{"action": <name>, "n": <integer, \
switch_workspace only>, "app": <string, launch_app only>, \
"confidence": <0.0-1.0>}}
- confidence is how sure you are that this is what the user meant.
- For launch_app, "app" is the application the user means; prefer a name \
from the alias or package lists above when one fits.
- When the user describes an application by what it does ("photo editor",
"music player"), pick the installed package that does that job; if no
installed package fits, use "none".
- Use action "none" for anything else, including workspace numbers \
outside 1-10.

Examples:
"Switch to workspace two." -> {{"action": "switch_workspace", "n": 2, "confidence": 0.98}}
"go to the ninth workspace" -> {{"action": "switch_workspace", "n": 9, "confidence": 0.95}}
"workspace 4" -> {{"action": "switch_workspace", "n": 4, "confidence": 0.9}}
"open a terminal" -> {{"action": "launch_app", "app": "terminal", "confidence": 0.97}}
"fire up the browser" -> {{"action": "launch_app", "app": "browser", "confidence": 0.9}}
"please lock my computer" -> {{"action": "lock_screen", "confidence": 0.95}}
"run updates" -> {{"action": "run_updates", "confidence": 0.97}}
"switch to workspace fifty" -> {{"action": "none", "confidence": 0.85}}
"workspace" -> {{"action": "none", "confidence": 0.6}}
"close workspace two" -> {{"action": "none", "confidence": 0.7}}
"make me a sandwich" -> {{"action": "none", "confidence": 0.95}}
"move this window to workspace five" -> {{"action": "none", "confidence": 0.8}}
"turn up the volume" -> {{"action": "none", "confidence": 0.9}}

Transcripts come from speech recognition and may contain extra punctuation, \
capitalization, or filler words."""

def render_schema() -> dict:
    """ Constrained decode, rendered from SPECS. One oneOf branch per action
    that takes args, so the grammar itself requires each action's args
    exactly when that action is chosen (a flat schema can't make "n"
    required for switch_workspace but absent for lock_screen, and a merely
    optional arg gets omitted by the model). Arg-less actions share one
    branch. Structure only, no ranges: a range constraint would truncate an
    out-of-range answer into a valid-looking one ("eleven" -> 1); ranges
    are validate()'s job, where a violation is refused, not mangled. """
    branches, no_args = [], []
    for name, spec in actions.SPECS.items():
        if not spec["args"]:
            no_args.append(name)
            continue
        props = {"action": {"const": name},
                 "confidence": {"type": "number"}}
        for arg, meta in spec["args"].items():
            props[arg] = {"type": meta["type"]}
        branches.append({"type": "object", "properties": props,
                         "required": ["action", *spec["args"], "confidence"]})
    branches.append({"type": "object",
                     "properties": {"action": {"enum": no_args},
                                    "confidence": {"type": "number"}},
                     "required": ["action", "confidence"]})
    return {"oneOf": branches}


SCHEMA = render_schema()   # SPECS is static; render once at import


def route_llm(text: str, ctx: Context, cfg: dict) -> dict:
    """ Ask the local model to map a transcript onto the action vocabulary.
    Returns its parsed JSON reply. Raises on transport or parse failure. """
    o = {**DEFAULTS, **cfg.get("ollama", {})}
    payload = json.dumps({
        "model": o["model"],
        "messages": [{"role": "system", "content": render_system_prompt(ctx)},
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


def validate(reply: dict, ctx: Context) -> Intent | None:
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
    if action == "launch_app":
        app = str(reply.get("app", "")).strip()
        if not app:
            raise ValueError("launch_app without an application name")
        # re-resolved here, never trusted from the model: the command that
        # runs is whatever THIS machine maps the name to, or nothing
        command, certain = actions.resolve_app(app, ctx)
        if command is None:
            raise ValueError(f"no such application {app!r}")
        return Intent("launch_app", {"app": app, "command": command},
                      confidence=confidence, uncertain=not certain)
    if action in ("lock_screen", "run_updates"):
        return Intent(action, {}, privileged=action in actions.PRIVILEGED,
                      confidence=confidence)
    raise ValueError(f"unknown action {action!r}")
