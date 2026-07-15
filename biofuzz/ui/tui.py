from __future__ import annotations

import atexit
import sys
import threading
import time
from collections import deque
from pathlib import Path

from biofuzz.storage import FuzzerLog

_ENTER_ALT_SCREEN = "\x1b[?1049h\x1b[?25l"
_EXIT_ALT_SCREEN = "\x1b[?25h\x1b[?1049l"
_CLEAR_AND_HOME = "\x1b[H\x1b[2J"


def _fmt_seconds(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class FuzzerTUI:
    def __init__(
        self,
        target: str,
        engine: str = "gnina",
        workers: int = 1,
        gpu_enabled: bool | None = None,
        log_path: str | Path | None = None,
        refresh_seconds: float = 0.25,
        heartbeat_seconds: float = 1.0,
        stream=None,
    ):
        self.target = target
        self.engine = engine
        self.workers = workers
        self.gpu_enabled = gpu_enabled
        self.refresh_seconds = refresh_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._stream = stream if stream is not None else sys.stdout

        self._log_path = Path(log_path) if log_path is not None else Path("runs") / target / "fuzzer.log"
        self._file_log = FuzzerLog(self._log_path)

        self._log_ring: deque[str] = deque(maxlen=10)
        self._last_status = None
        self._last_redraw = 0.0
        self._start_time = time.time()
        self._closed = False
        self._lock = threading.Lock()

        self._stream.write(_ENTER_ALT_SCREEN)
        self._stream.flush()
        atexit.register(self.close)

        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def update(self, status) -> None:
        with self._lock:
            self._last_status = status
        self._maybe_redraw()

    def log(self, message: str) -> None:
        with self._lock:
            self._log_ring.append(message)
        self._file_log.write("LOG", message)
        self._maybe_redraw(force=True)

    def notice(self, message: str) -> None:
        self.log(message)

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_seconds):
            self._maybe_redraw(force=True)

    def _maybe_redraw(self, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if self._closed:
                return
            if not force and (now - self._last_redraw) < self.refresh_seconds:
                return
            self._last_redraw = now
            rendered = self._render()

        self._stream.write(_CLEAR_AND_HOME)
        self._stream.write(rendered)
        self._stream.flush()

    def _render(self) -> str:
        status = self._last_status
        elapsed = _fmt_seconds(time.time() - self._start_time)
        gpu_str = "active" if self.gpu_enabled else ("inactive" if self.gpu_enabled is not None else "unknown")

        lines = [
            f"BioFuzz :: {self.target}",
            "-- Process Info " + "-" * 40,
            f"Runtime: {elapsed}   Engine: {self.engine}   GPU: {gpu_str}   Workers: {self.workers}",
        ]

        if status is not None:
            lines += [
                "-- Overall Results " + "-" * 37,
                f"Finds: {status.hits}   Bitmap Occ: {status.coverage_bitmap_occupancy:.3f}   "
                f"Epoch: {status.coverage_epoch}",
                f"Novelty S/W/N: {status.novelty_strong} / {status.novelty_weak} / {status.novelty_none}",
                "-- Progress " + "-" * 44,
                f"Docks Attempted: {status.total_docks}   Completed: {status.completed_docks}   "
                f"{status.docks_per_sec:.1f}/sec",
                f"Corpus: {status.corpus_size} entries",
                "-- Current " + "-" * 45,
                f"Best Affinity: {status.best_affinity}",
                f"Stage: {status.mutation_stage} / {status.mutation_type}   "
                f"Power: {status.power_score:.1f}   Budget: {status.mutation_budget}",
                f"Parent: {status.current_parent}",
            ]

        lines.append("-- Run Log " + "-" * 45)
        lines += list(self._log_ring)

        return "\n".join(lines) + "\n"

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._heartbeat_stop.set()
        self._file_log.close()
        self._stream.write(_EXIT_ALT_SCREEN)
        self._stream.flush()
