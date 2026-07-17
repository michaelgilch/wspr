"""wspr-i3: voice-command plugin for wspr.

Routes a command transcript through a local LLM (Ollama) onto a whitelisted
i3 action, validates, and executes. Loaded by wspr through the plugin seam
(module = "wspr_i3" under [command] in wspr.toml). Absorbed from hark
(github.com/michaelgilch/hark).

Verbs:
    exec TEXT...    route one transcript and execute it
"""

import sys
import threading

from . import actions, router

# wspr transcribes each utterance on its own thread. hark's serve loop handled
# transcripts strictly one at a time; this lock preserves that, so two
# in-flight utterances can't land i3 commands out of order.
_lock = threading.Lock()


def handle(text: str, cfg: dict) -> None:
    with _lock:
        print(f"heard: {text!r}")
        try:
            reply = router.route_llm(text, cfg)
        except Exception as e:
            print(f"  routing failed: {e}", file=sys.stderr)
            actions.notify("wspr ▸ " + text, "routing failed (is Ollama up?)")
            return
        print(f"  llm: {reply}")
        try:
            routed = router.validate(reply)
        except ValueError as e:
            print(f"  refused: {e}")
            actions.notify("wspr ▸ " + text, f"refused: {e}")
            return
        if routed is None:
            print("  no matching command")
            actions.notify("wspr ▸ " + text, "no matching command")
            return
        name, args = routed
        try:
            result = actions.ACTIONS[name](**args)
        except Exception as e:
            print(f"  action failed: {e}", file=sys.stderr)
            actions.notify("wspr", f"{name} failed: {e}")
            return
        print(f"  done: {result}")
        actions.notify("wspr ▸ " + text, result)


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
