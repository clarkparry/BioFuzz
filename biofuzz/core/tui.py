from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sys
import time
from typing import TextIO

ANSI_RESET = "\x1b[0m"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
HIT_LOG_RE = re.compile(
    r"^\[HIT\]\s+(?P<smiles>\S+)\s+\|\s+affinity=(?P<affinity>-?\d+(?:\.\d+)?)"
)


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
        self._run_log: deque[str] = deque(maxlen=10)
        self._last_render = 0.0
        self._cursor_hidden = False
        self._alt_screen_active = False
        self._status: RuntimeStatus | None = None
        self._last_find_elapsed_seconds: float | None = None

    def log(self, message: str) -> None:
        if not self.enabled:
            print(message, file=self.stream)
            return
        match = HIT_LOG_RE.match(message.strip())
        if match is None:
            return
        smiles = match.group("smiles")
        affinity_raw = match.group("affinity")
        try:
            affinity = f"{float(affinity_raw):.2f}"
        except ValueError:
            affinity = affinity_raw
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        self._run_log.appendleft(f"{timestamp}  {smiles}  {affinity}")
        if self._status is not None:
            self._last_find_elapsed_seconds = self._status.elapsed_seconds
        self.render(force=True)

    def notice(self, message: str) -> None:
        text = message.strip()
        if not text:
            return
        if not self.enabled:
            print(f"[INFO] {text}", file=self.stream)
            return
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        self._run_log.appendleft(f"{timestamp}  [INFO] {text}")
        self.render(force=True)

    def update(self, status: RuntimeStatus) -> None:
        self._status = status
        self.render(force=True)

    def render(self, force: bool = False) -> None:
        if not self.enabled or self._status is None:
            return
        now = time.monotonic()
        if not force and now - self._last_render < self.refresh_seconds:
            return
        self._last_render = now

        width = max(72, min(120, shutil.get_terminal_size((100, 24)).columns))
        lines = self._build_lines(width)

        if not self._alt_screen_active:
            self.stream.write("\x1b[?1049h")
            self._alt_screen_active = True
        if not self._cursor_hidden:
            self.stream.write("\x1b[?25l")
            self._cursor_hidden = True
        self.stream.write("\x1b[H\x1b[2J")
        self.stream.write("\n".join(lines))
        self.stream.flush()

    def close(self) -> None:
        if not self.enabled:
            return
        self.render(force=True)
        if self._cursor_hidden:
            self.stream.write("\x1b[?25h")
            self._cursor_hidden = False
        if self._alt_screen_active:
            self.stream.write("\x1b[?1049l")
            self._alt_screen_active = False
        self.stream.flush()

    def _build_lines(self, width: int) -> list[str]:
        status = self._status
        assert status is not None

        def paint(text: str, *codes: str) -> str:
            joined = ";".join(codes)
            return f"\x1b[{joined}m{text}{ANSI_RESET}"

        def visible_len(text: str) -> int:
            return len(ANSI_RE.sub("", text))

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

        def fmt_duration(seconds: float | None) -> str:
            if seconds is None:
                return "-"
            total = max(0, int(seconds))
            hours, rem = divmod(total, 3600)
            minutes, secs = divmod(rem, 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        def label(text: str) -> str:
            return paint(text, "1")

        def group_name(text: str) -> str:
            return paint(text, "1", "32")

        def red_label(text: str) -> str:
            return paint(text, "1", "31")

        def red_value(text: str) -> str:
            return paint(text, "31")

        def field(name: str, value: str) -> str:
            return f"{label(name)}: {value}"

        def finds_field(value: int) -> str:
            return f"{red_label('Finds')}: {red_value(str(value))}"

        def box(text: str = "") -> str:
            max_inner_width = width - 4
            body = text
            body_visible = visible_len(body)
            if body_visible > max_inner_width:
                plain = ANSI_RE.sub("", body)
                if max_inner_width > 3:
                    plain = f"{plain[: max_inner_width - 3]}..."
                else:
                    plain = plain[:max_inner_width]
                body = plain
                body_visible = len(body)
            return f"| {body}{' ' * (max_inner_width - body_visible)} |"

        def add_group(lines: list[str], name: str, rows: list[str], divider: str) -> None:
            lines.append(box(group_name(name)))
            for row in rows:
                lines.append(box(f"  {row}"))
            lines.append(divider)

        divider = "+" + ("-" * (width - 2)) + "+"
        since_last_find_seconds: float | None = None
        if self._last_find_elapsed_seconds is not None:
            since_last_find_seconds = max(0.0, status.elapsed_seconds - self._last_find_elapsed_seconds)

        lines = [divider, box(paint(f"BioFuzz :: {self.target}", "1", "34")), divider]

        add_group(
            lines,
            "Process Info",
            [
                f"{field('Runtime', fmt_duration(status.elapsed_seconds))}  "
                f"{field('Since Last Find', fmt_duration(since_last_find_seconds))}",
                f"{field('Engine', Path(self.engine).name)}  "
                f"{field('GPU', 'enabled' if self.gpu_enabled else 'disabled')}  "
                f"{field('Workers', str(self.workers))}",
            ],
            divider,
        )
        add_group(
            lines,
            "Overall Results",
            [
                f"{finds_field(status.finds)}  "
                f"{field('Coverage', f'{status.coverage_ratio:.3f}')}  "
                f"{field('Checkpoints', str(status.checkpoints))}",
            ],
            divider,
        )
        add_group(
            lines,
            "Progress",
            [
                f"{field('Total Docks', str(status.total_docks))}  "
                f"{field('Docks/sec', f'{status.docks_per_sec:.2f}')}",
                field("Corpus Size", str(status.corpus_size)),
            ],
            divider,
        )
        add_group(
            lines,
            "Findings In Depth",
            [
                field("Best Affinity", fmt_affinity(status.best_affinity)),
                field("Current Parent", shorten(status.current_parent, 56)),
                field("Current Candidate", shorten(status.current_smiles, 56)),
            ],
            divider,
        )
        add_group(
            lines,
            "State",
            [
                f"{field('Stage', status.stage)}  {field('Mutation Stage', status.mutation_stage or '-')}",
                f"{field('Mutation Type', status.mutation_type or '-')}  "
                f"{field('Power', f'{status.power_score:.2f}')}  "
                f"{field('Budget', str(status.mutation_budget))}",
            ],
            divider,
        )
        lines.append(box(group_name("Run Log")))
        if self._run_log:
            lines.extend(box(f"  {entry}") for entry in self._run_log)
        else:
            lines.append(box("  waiting for first hit..."))
        lines.append(divider)

        return lines
