from __future__ import annotations

import json
import sys
from dataclasses import asdict


class JSONStatusUI:
    """Emits each RuntimeStatus as newline-delimited JSON on stdout."""

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdout

    def update(self, status) -> None:
        self._stream.write(json.dumps(asdict(status)) + "\n")
        self._stream.flush()

    def log(self, message: str) -> None:
        self._stream.write(json.dumps({"log": message}) + "\n")
        self._stream.flush()

    def notice(self, message: str) -> None:
        self.log(message)

    def close(self) -> None:
        pass


class QuietUI:
    """Prints only hits and the final summary."""

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


class NoOpUI:
    """Attach nothing; the fuzzer runs silently."""

    def update(self, status) -> None:
        pass

    def log(self, message: str) -> None:
        pass

    def notice(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass
