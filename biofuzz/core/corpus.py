from __future__ import annotations

from dataclasses import asdict, dataclass
import heapq
import itertools
import json
from pathlib import Path


@dataclass
class CorpusEntry:
    smiles: str
    source_id: str
    priority: float
    times_mutated: int = 0
    best_affinity: float | None = None
    new_bits: int = 0


class Corpus:
    def __init__(self, max_size: int | None = None) -> None:
        self._heap: list[tuple[float, int, CorpusEntry]] = []
        self._counter = itertools.count()
        self._max_size = max_size if max_size is None else max(1, int(max_size))

    @property
    def max_size(self) -> int | None:
        return self._max_size

    def configure_max_size(self, max_size: int | None) -> None:
        self._max_size = None if max_size is None else max(1, int(max_size))
        self._trim_if_needed()

    def _trim_if_needed(self) -> None:
        while self._max_size is not None and len(self._heap) > self._max_size:
            # Drop the worst-priority entry to keep the queue bounded.
            lowest_idx = max(
                range(len(self._heap)),
                key=lambda idx: (self._heap[idx][0], -self._heap[idx][1]),
            )
            self._heap[lowest_idx] = self._heap[-1]
            self._heap.pop()
            heapq.heapify(self._heap)

    def add(self, entry: CorpusEntry) -> None:
        # Max-priority queue implemented as min-heap over negative priority.
        heapq.heappush(self._heap, (-float(entry.priority), next(self._counter), entry))
        self._trim_if_needed()

    def pop(self) -> CorpusEntry:
        if not self._heap:
            raise IndexError("Corpus is empty")
        _priority, _idx, entry = heapq.heappop(self._heap)
        return entry

    def size(self) -> int:
        return len(self._heap)

    def save(self, path: str | Path) -> None:
        entries = [asdict(item[2]) for item in sorted(self._heap, key=lambda tup: (tup[0], tup[1]))]
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"max_size": self._max_size, "entries": entries}, indent=2),
            encoding="utf-8",
        )

    def load(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self._heap.clear()
        self._counter = itertools.count()
        raw_max_size = payload.get("max_size")
        self._max_size = None if raw_max_size is None else max(1, int(raw_max_size))

        for raw in payload.get("entries", []):
            self.add(CorpusEntry(**raw))
