"""Machine grounding for wspr-i3.

Reads this machine's own configuration (declared packages, i3 workspace
names) and exposes it as a Context object. It feeds the LLM system prompt,
so the model chooses from this machine's vocabulary instead of the
internet's, and serves as the trust anchor for resolving spoken app names.
wspr-i3 reads what already governs the machine rather than maintaining a
parallel inventory.

Missing sources degrade, not crash: no packages.txt means an empty package
set, and everything else keeps routing. Grounding is enrichment, not a
dependency.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# Machine-specific paths. Lifting them into config is a noted follow-up
# for making wspr-i3 installable elsewhere.
PACKAGES_FILE = Path.home() / "arch-config" / "packages.txt"
I3_CONFIG = Path.home() / ".config" / "i3" / "config"
LOCK_SCRIPT = Path.home() / "dotfiles/bin/i3-lock"
UPDATE_SCRIPT = Path.home() / ".config/polybar/scripts/system-update-interactive.sh"

# Spoken alias -> launch command, curated from the i3 config's own launch
# bindings. Resolving through this map is the "certain" path for launch_app;
# anything else resolves fuzzily and is flagged for confirmation.
LAUNCH_MAP = {
    "terminal": "kitty",
    "kitty": "kitty",
    "browser": "google-chrome-stable",
    "chrome": "google-chrome-stable",
    "google chrome": "google-chrome-stable",
    "files": "thunar",
    "file manager": "thunar",
    "thunar": "thunar",
    "editor": "subl --new-window",
    "sublime": "subl --new-window",
    "sublime text": "subl --new-window",
}


@dataclass
class Context:
    host: str
    packages: set[str]          # packages declared for this host in arch-config
    workspaces: dict[int, str]  # i3 workspace number -> name
    launch_map: dict[str, str]


def read_host() -> str:
    try:
        return Path("/etc/hostname").read_text().strip()
    except OSError:
        return "unknown"


def read_packages(host: str) -> set[str]:
    """ Parse arch-config/packages.txt: untagged lines apply to every host,
    '[host] pkg' lines to that host only, an 'aur:' prefix marks AUR packages
    (stripped here; wspr-i3 only cares about the name). """
    pkgs: set[str] = set()
    try:
        lines = PACKAGES_FILE.read_text().splitlines()
    except OSError:
        return pkgs
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if m := re.match(r"^\[(\S+)\]\s+(.*)$", line):
            if m.group(1) != host:
                continue
            line = m.group(2).strip()
        line = line.removeprefix("aur:")
        if line:
            pkgs.add(line)
    return pkgs


def read_workspaces() -> dict[int, str]:
    """ Pull the `set $wsN "name"` declarations out of the i3 config. """
    ws: dict[int, str] = {}
    try:
        text = I3_CONFIG.read_text()
    except OSError:
        return ws
    for m in re.finditer(r'^set \$ws(\d+)\s+"([^"]+)"', text, re.M):
        ws[int(m.group(1))] = m.group(2)
    return ws


def build() -> Context:
    host = read_host()
    return Context(host, read_packages(host), read_workspaces(), dict(LAUNCH_MAP))
