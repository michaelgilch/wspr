"""Action whitelist and subprocess helpers for wspr-i3.

Owns everything that touches the desktop: i3 commands, desktop
notifications, and the rofi confirm gate. Knows nothing about routing;
router decides *what* to run, handle() in __init__.py decides *whether*
to run it.
"""

import subprocess
from pathlib import Path

# Machine-specific script paths. These move into context.py when machine
# grounding lands; lifting them into config is a noted follow-up.
LOCK_SCRIPT = Path.home() / "dotfiles/bin/i3-lock"
UPDATE_SCRIPT = Path.home() / ".config/polybar/scripts/system-update-interactive.sh"


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


# --- Actions ---------------------------------------------------------------

def switch_workspace(n: int) -> str:
    i3msg(f"workspace number {n}")
    return f"workspace {n}"


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
    "lock_screen": lock_screen,
    "run_updates": run_updates,
}

# Privileged actions confirm in EVERY confirm mode. A property of the
# action, not the routing: however certain the route, a human approves.
PRIVILEGED = {"run_updates"}
