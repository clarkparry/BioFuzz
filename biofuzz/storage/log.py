from __future__ import annotations

from datetime import datetime
from pathlib import Path


class FuzzerLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", buffering=1)

    def write(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._fh.write(f"{timestamp} [{level}] {message}\n")

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "FuzzerLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
