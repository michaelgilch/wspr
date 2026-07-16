"""Action whitelist and subprocess helpers for wspr-i3.

Owns everything that touches the desktop: i3 commands and desktop
notifications. Knows nothing about routing; router decides *what* to run,
handle() in __init__.py decides *whether* to run it.
"""

import subprocess


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
