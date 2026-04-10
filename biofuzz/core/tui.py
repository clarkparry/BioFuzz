from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
import time
from typing import TextIO


@dataclass(frozen=True)
class RuntimeStatus:
    stage: str
    mutation_stage: str
    mutation_type: str | None
    current_parent: str | None
    current_smiles: str | None
    power_score: float
    mutation_budget: int
    total_docks: int
    docks_per_sec: float
    corpus_size: int
    finds: int
    coverage_ratio: float
    best_affinity: float | None
    checkpoints: int
    elapsed_seconds: float


class FuzzerTUI:
    def __init__(
        self,
        target: str,
        engine: str,
        workers: int,
        gpu_enabled: bool,
        stream: TextIO | None = None,
        refresh_seconds: float = 0.25,
        enabled: bool | None = None,
    ) -> None:
        self.target = target
        self.engine = engine
        self.workers = workers
        self.gpu_enabled = gpu_enabled
        self.stream = stream or sys.stdout
        self.refresh_seconds = max(0.05, refresh_seconds)
        self.enabled = bool(enabled) if enabled is not None else self.stream.isatty()
        self._events: deque[str] = deque(maxlen=6)
        self._last_render = 0.0
        self._cursor_hidden = False
        self._status: RuntimeStatus | None = None

    def log(self, message: str) -> None:
        if not self.enabled:
            print(message, file=self.stream)
            return
        self._events.appendleft(message)
        self.render(force=True)

    def update(self, status: RuntimeStatus) -> None:
        self._status = status
        self.render()

    def render(self, force: bool = False) -> None:
        if not self.enabled or self._status is None:
            return
        now = time.monotonic()
        if not force and now - self._last_render < self.refresh_seconds:
            return
        self._last_render = now

        width = max(72, min(120, shutil.get_terminal_size((100, 24)).columns))
        lines = self._build_lines(width)

        if not self._cursor_hidden:
            self.stream.write("\x1b[?25l")
            self._cursor_hidden = True
        self.stream.write("\x1b[H\x1b[2J")
        self.stream.write("\n".join(lines))
        self.stream.write("\n")
        self.stream.flush()

    def close(self) -> None:
        if not self.enabled:
            return
        self.render(force=True)
        if self._cursor_hidden:
            self.stream.write("\x1b[?25h\n")
            self.stream.flush()
            self._cursor_hidden = False

    def _build_lines(self, width: int) -> list[str]:
        status = self._status
        assert status is not None

        def shorten(value: str | None, limit: int = 30) -> str:
            if not value:
                return "-"
            if len(value) <= limit:
                return value
            return f"{value[: limit - 3]}..."

        def fmt_affinity(value: float | None) -> str:
            if value is None:
                return "-"
            return f"{value:.2f} kcal/mol"

        def fmt_uptime(seconds: float) -> str:
            total = max(0, int(seconds))
            hours, rem = divmod(total, 3600)
            minutes, secs = divmod(rem, 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        def box(text: str = "") -> str:
            body = text[: width - 4]
            return f"| {body:<{width - 4}} |"

        title = f" BioFuzz AFL-style TUI :: {self.target} "
        top = "+" + title[: width - 2].ljust(width - 2, "-") + "+"
        bottom = "+" + ("-" * (width - 2)) + "+"

        mutation = status.mutation_type or "-"
        lines = [
            top,
            box(
                f"engine={Path(self.engine).name}  gpu={'enabled' if self.gpu_enabled else 'disabled'}  "
                f"workers={self.workers}  stage={status.stage}"
            ),
            box(
                f"mutation_stage={status.mutation_stage or '-'}  mutation_type={mutation}  "
                f"power={status.power_score:.2f}  budget={status.mutation_budget}"
            ),
            box(
                f"total_docks={status.total_docks}  docks/sec={status.docks_per_sec:.2f}  "
                f"corpus={status.corpus_size}  finds={status.finds}"
            ),
            box(
                f"coverage={status.coverage_ratio:.3f}  best_affinity={fmt_affinity(status.best_affinity)}  "
                f"checkpoints={status.checkpoints}"
            ),
            box(f"parent={shorten(status.current_parent, 48)}"),
            box(f"candidate={shorten(status.current_smiles, 48)}"),
            box(f"uptime={fmt_uptime(status.elapsed_seconds)}"),
            bottom,
            "Recent events:",
        ]

        if self._events:
            lines.extend(f"  {shorten(message, width - 4)}" for message in self._events)
        else:
            lines.append("  waiting for first event...")

        return lines
