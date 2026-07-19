"""Action whitelist and subprocess helpers for wspr-i3.

Owns everything that touches the desktop: i3 commands, desktop
notifications, and the rofi confirm gate. Knows nothing about routing;
router decides *what* to run, handle() in __init__.py decides *whether*
to run it.
"""

import shutil
import subprocess

from . import i3
from .context import Context, LOCK_SCRIPT, UPDATE_SCRIPT

# The window-manager transport. Actions are plain functions, so they take
# their backend from this module-level slot; the plugin surface replaces it
# from config at first use.
BACKEND: i3.Backend = i3.I3MsgBackend()


# --- Helpers ----------------------------------------------------------------

def notify(summary: str, body: str = "") -> None:
    """ Desktop notification """
    subprocess.run(["notify-send", summary, body], check=False)


def confirm(prompt: str) -> bool:
    """ Human gate: rofi yes/no. 'No' is listed first so a reflexive Enter
    cancels rather than approves. Fails closed if rofi is unavailable. """
    try:
        out = subprocess.run(
            ["rofi", "-dmenu", "-p", prompt, "-no-custom"],
            input="No\nYes\n", capture_output=True, text=True, check=False)
        return out.stdout.strip() == "Yes"
    except FileNotFoundError:
        return False


# --- App resolution ---------------------------------------------------------

def resolve_app(app: str, ctx: Context) -> tuple[str | None, bool]:
    """ Map a spoken application name to a launch command.

    Returns (command, certain): a trust gradient, not a boolean. A curated
    launch-map hit is certain, as trustworthy as a keybinding. A bare match
    against an installed binary still launches but is flagged so 'uncertain'
    confirm mode asks first. (None, False) means nothing resolved: the
    action is refused, not guessed at.
    """
    name = app.strip().lower()
    if name in ctx.launch_map:
        return ctx.launch_map[name], True
    # an exact curated command (or its binary) is just as certain as the alias
    if name in ctx.launch_map.values():
        return name, True
    curated = {cmd.split()[0] for cmd in ctx.launch_map.values()}
    for token in (name.replace(" ", "-"), name.replace(" ", "")):
        if shutil.which(token):
            return token, token in curated
    return None, False


# --- Actions ---------------------------------------------------------------

def switch_workspace(n: int) -> str:
    ok = BACKEND.command(f"workspace number {n}")
    return f"workspace {n}" if ok else f"i3 refused: workspace {n}"


def launch_app(app: str, command: str) -> str:
    # command comes from the curated map or a which() hit, never raw model
    # output, but refuse quoting/injection characters outright anyway:
    # i3-msg exec hands the string to a shell.
    if any(c in command for c in ";,$`'\""):
        return f"refused suspicious command: {command}"
    ok = BACKEND.command(f"exec --no-startup-id {command}")
    if not ok:
        return f"i3 refused to launch {command}"
    return f"launching {app}" if app == command else f"launching {app} ({command})"


def move_to_workspace(n: int) -> str:
    # i3's move container operates on the focused window; no query needed
    ok = BACKEND.command(f"move container to workspace number {n}")
    return f"moved window to workspace {n}" if ok else "i3 refused the move"


def focus_window(query: str) -> str:
    """ Find a window by app name or title words. Policy for ambiguity:
    0 matches -> refuse; 1 -> act; many -> ask (rofi picker). A wrong
    guess costs more trust than a picker costs time. """
    q = query.strip().lower()
    wins = [w for w in BACKEND.windows()
            if q in w.window_class.lower() or q in w.title.lower()]
    if not wins:
        return f"no window matches {query!r}"
    win = wins[0] if len(wins) == 1 else pick_window(wins)
    if win is None:
        return "cancelled"
    # the spoken query never reaches a shell: it is matched in Python, and
    # the i3 command uses only the matched window's numeric con_id
    BACKEND.command(f"[con_id={win.con_id}] focus")
    return f"focused {win.window_class or win.title}"


def pick_window(wins: list[i3.Window]) -> i3.Window | None:
    """ Human disambiguation: a rofi line per candidate, index comes back. """
    lines = "\n".join(f"[{w.workspace}] {w.window_class}: {w.title[:60]}"
                      for w in wins)
    out = subprocess.run(
        ["rofi", "-dmenu", "-p", "which window?", "-no-custom", "-format", "i"],
        input=lines, capture_output=True, text=True, check=False)
    try:
        return wins[int(out.stdout.strip())]
    except (ValueError, IndexError):
        return None


def lock_screen() -> str:
    # Detached into its own session so it neither dies with wspr nor
    # becomes a zombie wspr must reap.
    subprocess.Popen([str(LOCK_SCRIPT)], start_new_session=True)
    return "locking screen"


def run_updates() -> str:
    # Same invocation as the polybar updates module uses.
    subprocess.Popen(["kitty", "--title", "System Updates", "-e",
                      str(UPDATE_SCRIPT)], start_new_session=True)
    return "running system updates"


# The whitelist: the only things the command sink can do.
# The LLM picks by name. validate() decides whether the pick may run.
ACTIONS = {
    "switch_workspace": switch_workspace,
    "move_to_workspace": move_to_workspace,
    "focus_window": focus_window,
    "launch_app": launch_app,
    "lock_screen": lock_screen,
    "run_updates": run_updates,
}

# Privileged actions confirm in EVERY confirm mode. A property of the
# action, not the routing: however certain the route, a human approves.
PRIVILEGED = {"run_updates"}

# What the LLM is told it may emit: the single source of truth from which
# the router renders both the system prompt's action list and the reply
# schema. Add an action here (plus its ACTIONS entry and validate() arm)
# and the prompt and grammar stay in sync by construction.
# Each arg: name -> {"type": JSON-schema type, "desc": prompt description}.
SPECS = {
    "switch_workspace": {
        "args": {"n": {"type": "integer", "desc": "workspace number 1-10"}},
        "desc": "focus i3 workspace number n",
    },
    "move_to_workspace": {
        "args": {"n": {"type": "integer", "desc": "workspace number 1-10"}},
        "desc": "move the currently focused window to workspace n "
                "(only when the user names an object: this, this window, it)",
    },
    "focus_window": {
        "args": {"query": {"type": "string",
                           "desc": "app name or window title words"}},
        "desc": "focus an already-open window matching the query",
    },
    "launch_app": {
        "args": {"app": {"type": "string", "desc": "application name"}},
        "desc": "open an application",
    },
    "lock_screen": {"args": {}, "desc": "lock the screen"},
    "run_updates": {"args": {},
                    "desc": "open the interactive system update window"},
    "none": {"args": {},
             "desc": "the request does not map to any available action"},
}
