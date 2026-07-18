"""wspr-i3: voice-command plugin for wspr.

Routes a command transcript through a local LLM (Ollama) onto a whitelisted
i3 action, validates, and executes. Loaded by wspr through the plugin seam
(module = "wspr_i3" under [command] in wspr.toml).

Verbs:
    route TEXT...   dry run: print the routed action, execute nothing
    exec TEXT...    route one transcript and execute it
"""

import sys
import threading

from . import actions, router

# wspr transcribes each utterance on its own thread.
_lock = threading.Lock()


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
        notify = (lambda *a: None) if dry_run else actions.notify
        print(f"heard: {text!r}")
        try:
            reply = router.route_llm(text, cfg)
        except Exception as e:
            print(f"  routing failed: {e}", file=sys.stderr)
            notify("wspr ▸ " + text, "routing failed (is Ollama up?)")
            return
        print(f"  llm: {reply}")
        try:
            intent = router.validate(reply)
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
    print(__doc__.strip(), file=sys.stderr)
    return 1
