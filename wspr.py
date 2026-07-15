"""wspr - push-to-talk voice tool.

Usage:
    wspr                    run the push-to-talk daemon
    wspr COMMAND [ARG...]   forwarded to the command plugin named by [command]
                            in wspr.toml

Thin entry point: config loading, plugin loading, and dispatch. Imports here
are stdlib-only so subcommands start fast; the daemon's heavy imports (numpy,
sounddevice, faster-whisper, Xlib) live in daemon.py and load only for the
bare `wspr` invocation.
"""

import importlib
import os
import sys
import tomllib
from pathlib import Path

# Out-of-repo plugins live here; the app directory itself (this file's
# directory) is already on sys.path as the script directory.
PLUGIN_DIR = Path.home() / ".local" / "share" / "wspr" / "plugins"


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


def load_plugin(cfg: dict):
    """Import and return the command plugin named by [command] module.

    Exits when no module is configured or the import fails.
    """
    name = cfg.get("command", {}).get("module")
    if not name:
        sys.exit('No command plugin configured: set module = "..." under '
                 "[command] in wspr.toml (the i3 plugin ships as wspr_i3).")
    if PLUGIN_DIR.is_dir() and str(PLUGIN_DIR) not in sys.path:
        sys.path.append(str(PLUGIN_DIR))
    try:
        return importlib.import_module(name)
    except ImportError as e:
        sys.exit(f"Command plugin {name!r} failed to import ({e}): "
                 "Check module under [command] in wspr.toml, or reinstall.")


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        import daemon
        daemon.run(load_config())
        return
    if argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    # Every subcommand belongs to the plugin; core stays verb-agnostic.
    cfg = load_config()
    plugin = load_plugin(cfg)
    if not hasattr(plugin, "cli"):
        sys.exit(f"Command plugin {plugin.__name__!r} provides no cli().")
    sys.exit(plugin.cli(argv, cfg))


if __name__ == "__main__":
    main()
