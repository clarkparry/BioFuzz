from __future__ import annotations

import sys


class QuietUI:
    """Prints hits and nothing else.

    The non-interactive display, chosen automatically when stdout is not a
    TTY: a campaign piped to a file should leave the findings behind and no
    redrawing status frames. Attaching no UI at all, by leaving
    `Campaign.ui` as None, makes the campaign silent.
    """

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdout

    def update(self, status) -> None:
        pass

    def log(self, message: str) -> None:
        if message.startswith("[HIT]") or "affinity=" in message:
            self._stream.write(message + "\n")
            self._stream.flush()

    def notice(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass
