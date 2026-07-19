"""Action whitelist and subprocess helpers for wspr-i3.

Owns everything that touches the desktop: i3 commands, desktop
notifications, and the rofi confirm gate. Knows nothing about routing;
router decides *what* to run, handle() in __init__.py decides *whether*
to run it.
"""

import shutil
import subprocess

from .context import Context, LOCK_SCRIPT, UPDATE_SCRIPT


# --- Helpers ----------------------------------------------------------------

def i3msg(*cmds: str) -> None:
    """ Send one or more commands to i3 in a single i3-msg call """
    subprocess.run(["i3-msg", "; ".join(cmds)], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    i3msg(f"workspace number {n}")
    return f"workspace {n}"


def launch_app(app: str, command: str) -> str:
    # command comes from the curated map or a which() hit, never raw model
    # output, but refuse quoting/injection characters outright anyway:
    # i3-msg exec hands the string to a shell.
    if any(c in command for c in ";,$`'\""):
        return f"refused suspicious command: {command}"
    i3msg(f"exec --no-startup-id {command}")
    return f"launching {app}" if app == command else f"launching {app} ({command})"


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
