#!/usr/bin/env python3
"""eval - routing accuracy battery for wspr-i3's LLM router.

Runs a fixed set of transcripts through the real router (prompt, schema,
validate) and grades the outcomes. Use it to judge a prompt change or a
model swap: run before, run after, compare.

Each case allows one or more acceptable outcomes:
  ("switch_workspace", {"n": 2})  an Intent with these args (subset match)
  "none"                          the model deliberately answered none
  "refused"                       validate() raised (out of range, unknown app)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wspr                            # noqa: E402
from wspr_i3 import context, router    # noqa: E402

# (transcript, acceptable outcomes). Comments say what each case probes.
CASES = [
    # workspace switching: phrasing spread
    ("Switch to workspace two.", [("switch_workspace", {"n": 2})]),
    ("go to the fifth workspace", [("switch_workspace", {"n": 5})]),
    ("workspace 3", [("switch_workspace", {"n": 3})]),
    ("Please move to workspace ten.", [("switch_workspace", {"n": 10})]),
    ("jump over to workspace nine", [("switch_workspace", {"n": 9})]),
    ("take me to workspace six", [("switch_workspace", {"n": 6})]),
    # workspace edge cases
    ("switch to workspace eleven", ["refused", "none"]),   # never n=1
    ("workspace", ["none"]),
    ("close workspace two", ["none"]),
    ("what workspace am I on", ["none"]),
    # launches: curated aliases
    ("open a terminal", [("launch_app", {"command": "kitty"})]),
    ("open kitty", [("launch_app", {"command": "kitty"})]),
    ("fire up the browser", [("launch_app", {"command": "google-chrome-stable"})]),
    ("open google chrome", [("launch_app", {"command": "google-chrome-stable"})]),
    ("open the file manager", [("launch_app", {"command": "thunar"})]),
    ("open sublime", [("launch_app", {"command": "subl --new-window"})]),
    # launches: installed but uncurated / uninstalled / descriptive
    ("open audacity", [("launch_app", {"command": "audacity"})]),
    ("open blender", ["refused", "none"]),                 # not installed
    ("open my photo editor", [("launch_app", {"command": "gimp"})]),
    # lock / updates phrasing spread
    ("lock the screen", [("lock_screen", {})]),
    ("please lock my computer", [("lock_screen", {})]),
    ("run updates", [("run_updates", {})]),
    ("update the system", [("run_updates", {})]),
    # window management: object words decide move vs switch
    ("move this window to workspace five", [("move_to_workspace", {"n": 5})]),
    ("put this on workspace three", [("move_to_workspace", {"n": 3})]),
    ("move to workspace five", [("switch_workspace", {"n": 5})]),  # no object: you move
    ("focus chrome", [("focus_window", {})]),
    ("show me the browser", [("focus_window", {})]),
    # nonsense and near-misses
    ("make me a sandwich", ["none"]),
]


def outcome(text: str, ctx, cfg) -> tuple[object, float]:
    start = time.perf_counter()
    try:
        reply = router.route_llm(text, ctx, cfg)
    except Exception as e:
        return f"transport error: {e}", time.perf_counter() - start
    elapsed = time.perf_counter() - start
    try:
        intent = router.validate(reply, ctx)
    except ValueError:
        return "refused", elapsed
    if intent is None:
        return "none", elapsed
    return (intent.name, intent.args), elapsed


def matches(got: object, want: object) -> bool:
    if isinstance(want, str):
        return got == want
    if not isinstance(got, tuple):
        return False
    name, args = want
    return got[0] == name and all(got[1].get(k) == v for k, v in args.items())


def main() -> None:
    ctx = context.build()
    cfg = wspr.load_config()
    model = {**router.DEFAULTS, **cfg.get("ollama", {})}["model"]
    passed, times = 0, []
    for text, acceptable in CASES:
        got, secs = outcome(text, ctx, cfg)
        times.append(secs)
        ok = any(matches(got, want) for want in acceptable)
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {secs*1000:6.0f} ms  {text!r:42} -> {got}")
    n = len(CASES)
    print(f"\n{passed}/{n} passed ({passed/n:.0%})  "
          f"model={model}  "
          f"mean {sum(times)/n*1000:.0f} ms  max {max(times)*1000:.0f} ms")
    sys.exit(0 if passed == n else 1)


if __name__ == "__main__":
    main()
