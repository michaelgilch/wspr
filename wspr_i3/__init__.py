"""wspr-i3: voice-command plugin for wspr.

Routes a command transcript through a local LLM (Ollama) onto a whitelisted
i3 action, validates, and executes. Loaded by wspr through the plugin seam
(module = "wspr_i3" under [command] in wspr.toml).

Verbs:
    route TEXT...   dry run: print the routed action, execute nothing
    exec TEXT...    route one transcript and execute it
    context         show what wspr-i3 knows about this machine
    windows         list the windows wspr-i3 sees (debug)
"""

import sys
import threading

from . import actions, context, i3, router

# wspr transcribes each utterance on its own thread.
_lock = threading.Lock()

# Machine grounding, built once on first use and reused; handle() holds
# _lock during routing, which also serializes the first build.
_CTX: context.Context | None = None


def _ctx() -> context.Context:
    global _CTX
    if _CTX is None:
        _CTX = context.build()
    return _CTX


# The window-manager transport is chosen by config once per process.
# get_backend fails loud if config names i3ipc without the package.
_backend_wired = False


def _wire_backend(cfg: dict) -> None:
    global _backend_wired
    if not _backend_wired:
        actions.BACKEND = i3.get_backend(cfg)
        _backend_wired = True


def needs_confirm(intent: router.Intent, cfg: dict) -> bool:
    # Config defaults are read locally here until prepare() formalizes
    # merging at daemon startup.
    if intent.privileged:
        return True                       # privileged ALWAYS confirms
    mode = cfg.get("confirm", {}).get("mode", "uncertain")
    if mode == "always":
        return True
    if mode == "never":
        return False
    # 'uncertain': ask when the routing itself was shaky
    threshold = {**router.DEFAULTS, **cfg.get("ollama", {})}["confidence_threshold"]
    return intent.uncertain or intent.confidence < threshold


def handle(text: str, cfg: dict, dry_run: bool = False) -> None:
    with _lock:
        _wire_backend(cfg)
        notify = (lambda *a: None) if dry_run else actions.notify
        print(f"heard: {text!r}")
        ctx = _ctx()
        try:
            reply = router.route_llm(text, ctx, cfg)
        except Exception as e:
            print(f"  routing failed: {e}", file=sys.stderr)
            notify("wspr ▸ " + text, "routing failed (is Ollama up?)")
            return
        print(f"  llm: {reply}")
        try:
            intent = router.validate(reply, ctx)
        except ValueError as e:
            print(f"  refused: {e}")
            notify("wspr ▸ " + text, f"refused: {e}")
            return
        if intent is None:
            print("  no matching command")
            notify("wspr ▸ " + text, "no matching command")
            return
        intent.heard = text
        print(f"  intent: {intent.describe()}  [privileged={intent.privileged} "
              f"confidence={intent.confidence:.2f} "
              f"confirm={needs_confirm(intent, cfg)}]")
        if dry_run:
            print("  dry run: not executing")
            return
        if needs_confirm(intent, cfg) and not actions.confirm(f"{intent.describe()}?"):
            print("  cancelled")
            notify("wspr", "cancelled")
            return
        try:
            result = actions.ACTIONS[intent.name](**intent.args)
        except Exception as e:
            print(f"  action failed: {e}", file=sys.stderr)
            notify("wspr", f"{intent.name} failed: {e}")
            return
        print(f"  done: {result}")
        notify("wspr ▸ " + text, result)


# --- wspr CLI surface -------------------------------------------------------

def cli(argv: list[str], cfg: dict) -> int:
    """ Dispatch a wspr subcommand (argv is sys.argv[1:] from the wspr CLI).
    Returns a process exit code. """
    cmd, *rest = argv
    text = " ".join(rest).strip()
    if cmd in ("route", "exec") and text:
        handle(text, cfg, dry_run=(cmd == "route"))
        return 0
    if cmd == "context" and not rest:
        ctx = _ctx()
        print(f"host:       {ctx.host}")
        print(f"packages:   {len(ctx.packages)} declared")
        print(f"workspaces: {ctx.workspaces}")
        print(f"launch map: {len(ctx.launch_map)} aliases")
        return 0
    if cmd == "windows" and not rest:
        _wire_backend(cfg)
        for w in actions.BACKEND.windows():
            mark = "*" if w.focused else " "
            print(f"{mark} [{w.workspace}] {w.window_class}: {w.title[:60]}")
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 1
