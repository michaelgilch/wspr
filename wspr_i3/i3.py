"""Typed access to the window manager, behind a swappable backend.

Actions talk to the Backend interface; whether the transport is an i3-msg
subprocess (zero dependencies) or the i3ipc library (persistent socket,
event-capable) is a config choice, not a code change. The protocol grows
only when a new action needs something no current method provides.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Workspace:
    num: int
    name: str
    focused: bool
    visible: bool


@dataclass
class Window:
    con_id: int          # i3 container id: stable handle for [con_id=...] selectors
    window_class: str
    title: str
    workspace: str
    focused: bool


class Backend(Protocol):
    """ What actions may ask of the window manager. """

    def command(self, cmd: str) -> bool: ...
    def workspaces(self) -> list[Workspace]: ...
    def windows(self) -> list[Window]: ...


class I3MsgBackend:
    """ Zero-dependency transport: one i3-msg subprocess per call. """

    def command(self, cmd: str) -> bool:
        # i3 replies [{"success": true}, ...], one object per ;-joined
        # command. Piping this to DEVNULL made typo'd commands vanish
        # silently; reading the reply lets actions report failure.
        out = subprocess.run(["i3-msg", cmd], capture_output=True, text=True)
        try:
            return all(r.get("success") for r in json.loads(out.stdout))
        except (json.JSONDecodeError, TypeError):
            return False

    def workspaces(self) -> list[Workspace]:
        out = subprocess.run(["i3-msg", "-t", "get_workspaces"],
                             capture_output=True, text=True)
        return [Workspace(w["num"], w["name"], w["focused"], w["visible"])
                for w in json.loads(out.stdout)]

    def windows(self) -> list[Window]:
        # get_tree is the ground truth: i3's whole layout as JSON
        # (outputs -> workspaces -> containers -> windows).
        out = subprocess.run(["i3-msg", "-t", "get_tree"],
                             capture_output=True, text=True)
        return list(_walk(json.loads(out.stdout), workspace=""))


class I3ipcBackend:
    """ Persistent IPC connection via the i3ipc package (pacman:
    python-i3ipc). Same interface; also the doorway to event subscription
    (window::new, workspace::focus), which is the one capability the
    stdlib transport can never offer. """

    def __init__(self) -> None:
        import i3ipc                    # deferred: only pay for it if selected
        # i3 restarts (config reloads) sever the IPC socket; reconnect
        # transparently instead of needing daemon restart logic.
        self._conn = i3ipc.Connection(auto_reconnect=True)

    def command(self, cmd: str) -> bool:
        return all(r.success for r in self._conn.command(cmd))

    def workspaces(self) -> list[Workspace]:
        return [Workspace(w.num, w.name, w.focused, w.visible)
                for w in self._conn.get_workspaces()]

    def windows(self) -> list[Window]:
        wins = []
        for con in self._conn.get_tree().descendants():
            if con.window is None:      # layout containers, not X windows
                continue
            ws = con.workspace()
            wins.append(Window(con.id, con.window_class or "",
                               con.name or "", ws.name if ws else "",
                               con.focused))
        return wins


def get_backend(cfg: dict) -> Backend:
    """ The whole payoff of the seam: the transport is a config line.
    An unknown or missing package fails loud at startup, not mid-command. """
    if cfg.get("i3", {}).get("backend") == "i3ipc":
        return I3ipcBackend()
    return I3MsgBackend()


def _walk(node: dict, workspace: str):
    """ Depth-first over the layout tree, remembering the nearest enclosing
    workspace. A con is a real X window when it carries a window id;
    layout containers do not. """
    if node.get("type") == "workspace":
        workspace = node.get("name", workspace)
    if node.get("window") is not None:
        props = node.get("window_properties") or {}
        yield Window(node["id"], props.get("class", ""),
                     node.get("name") or "", workspace,
                     node.get("focused", False))
    # floating windows live in a separate child list; forgetting it makes
    # dialogs invisible to wspr
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        yield from _walk(child, workspace)
