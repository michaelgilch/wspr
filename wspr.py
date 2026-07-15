"""wspr - push-to-talk voice tool.

Thin entry point: argument parsing, config loading, and dispatch. Imports
here are stdlib-only so subcommands start fast; the daemon's heavy imports
(numpy, sounddevice, faster-whisper, Xlib) live in daemon.py and load only
for the bare `wspr` invocation.
"""

import argparse
import os
import tomllib
from pathlib import Path


def find_config_path() -> Path | None:
    """Return the config path to load, or None if there is none.

    Walks the priority cascade and returns the first existing config path, or
    None. ($WSPR_CONFIG --> ./wspr.toml --> ~/.config/wspr/wspr.toml)
    """
    env = os.environ.get("WSPR_CONFIG")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent / "wspr.toml"
    if here.exists():
        return here
    xdg = Path.home() / ".config" / "wspr" / "wspr.toml"
    if xdg.exists():
        return xdg
    return None


def load_config() -> dict:
    """Load the TOML config from the cascade, or {} when there is none."""
    path = find_config_path()
    if path is None or not path.exists():
        return {}
    with open(path, "rb") as f:       # tomllib insists on binary mode
        cfg = tomllib.load(f)
    print(f"Loaded config from {path}")
    return cfg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wspr",
        description="Push-to-talk dictation daemon. Run with no arguments.")
    sub = parser.add_subparsers(dest="cmd")
    p_exec = sub.add_parser(
        "exec", help="route one transcript through the command sink and execute it")
    p_exec.add_argument("text", nargs="+", help="the transcript to route")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cmd == "exec":
        import command
        command.handle(" ".join(args.text), load_config())
        return
    import daemon
    daemon.run(load_config())


if __name__ == "__main__":
    main()
