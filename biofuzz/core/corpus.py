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
    finds: int = 0


class Corpus:
    def __init__(self, max_size: int | None = None) -> None:
        self._heap: list[tuple[float, int, str]] = []
        self._entries: dict[str, CorpusEntry] = {}
        self._tokens: dict[str, int] = {}
        self._counter = itertools.count()
        self._max_size = max_size if max_size is None else max(1, int(max_size))

    @property
    def max_size(self) -> int | None:
        return self._max_size

    def configure_max_size(self, max_size: int | None) -> None:
        self._max_size = None if max_size is None else max(1, int(max_size))
        self._trim_if_needed()

    def _trim_if_needed(self) -> None:
        while self._max_size is not None and len(self._entries) > self._max_size:
            # Drop the worst-priority entry to keep the queue bounded.
            lowest_entry = min(
                self._entries.values(),
                key=lambda entry: (entry.priority, entry.times_mutated, entry.smiles),
            )
            self._entries.pop(lowest_entry.smiles, None)
            self._tokens.pop(lowest_entry.smiles, None)

    def _push(self, entry: CorpusEntry) -> None:
        token = next(self._counter)
        self._entries[entry.smiles] = entry
        self._tokens[entry.smiles] = token
        heapq.heappush(self._heap, (-float(entry.priority), token, entry.smiles))

    def _merge_entry(self, existing: CorpusEntry, new_entry: CorpusEntry) -> CorpusEntry:
        affinity: float | None
        if existing.best_affinity is None:
            affinity = new_entry.best_affinity
        elif new_entry.best_affinity is None:
            affinity = existing.best_affinity
        else:
            affinity = min(existing.best_affinity, new_entry.best_affinity)

        source_id = existing.source_id
        if source_id.startswith("mutant_of:") and not new_entry.source_id.startswith("mutant_of:"):
            source_id = new_entry.source_id

        return CorpusEntry(
            smiles=existing.smiles,
            source_id=source_id,
            priority=max(existing.priority, new_entry.priority),
            times_mutated=max(existing.times_mutated, new_entry.times_mutated),
            best_affinity=affinity,
            new_bits=max(existing.new_bits, new_entry.new_bits),
            finds=max(existing.finds, new_entry.finds),
        )

    def add(self, entry: CorpusEntry) -> None:
        existing = self._entries.get(entry.smiles)
        merged = self._merge_entry(existing, entry) if existing is not None else entry
        # Max-priority queue implemented as min-heap over negative priority.
        self._push(merged)
        self._trim_if_needed()

    def pop(self) -> CorpusEntry:
        while self._heap:
            _priority, token, smiles = heapq.heappop(self._heap)
            current_token = self._tokens.get(smiles)
            if current_token != token:
                continue
            entry = self._entries.pop(smiles)
            self._tokens.pop(smiles, None)
            return entry
        raise IndexError("Corpus is empty")

    def size(self) -> int:
        return len(self._entries)

    def save(self, path: str | Path) -> None:
        entries = [
            asdict(entry)
            for entry in sorted(
                self._entries.values(),
                key=lambda item: (-item.priority, item.times_mutated, item.smiles),
            )
        ]
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"max_size": self._max_size, "entries": entries}, indent=2),
            encoding="utf-8",
        )

    def load(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self._heap.clear()
        self._entries.clear()
        self._tokens.clear()
        self._counter = itertools.count()
        raw_max_size = payload.get("max_size")
        self._max_size = None if raw_max_size is None else max(1, int(raw_max_size))

        for raw in payload.get("entries", []):
            self.add(CorpusEntry(**raw))
