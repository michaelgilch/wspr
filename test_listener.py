#!/usr/bin/env python3
"""Test listener for testing wspr's socket sink.

Binds the same Unix socket wspr's socket sink connects to, and prints whatever
arrives. Run this in one terminal, run wspr in another (with a socket-routed
hotkey), then dictate; the transcript shows up here.
"""

import os
import socket
from pathlib import Path

# Must match DEFAULT_SOCKET in wspr.py
SOCKET_PATH = str(Path(os.environ["XDG_RUNTIME_DIR"]) / "wspr.sock")


def main() -> None:
    # A leftover socket file from a previous run would make bind() fail with
    # "address already in use", so remove a stale one first.
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen()
    print(f"Listening on {SOCKET_PATH}. Ctrl-C to quit.")

    try:
        while True:
            # Each transcript is one connection: accept it, read until the
            # sender closes, print, and loop for the next.
            conn, _ = server.accept()
            with conn:
                chunks = []
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                text = b"".join(chunks).decode()
                if text:
                    print(f"  received: {text!r}")
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        server.close()
        os.unlink(SOCKET_PATH)


if __name__ == "__main__":
    main()
