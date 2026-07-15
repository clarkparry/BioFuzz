from __future__ import annotations

import heapq
import itertools
import random
from dataclasses import asdict

from biofuzz.corpus.entry import CorpusEntry
from biofuzz.corpus.scheduler import compute_priority
from biofuzz.corpus.smiles_canon import canonicalize
from biofuzz.storage.checkpoint import load_checkpoint, save_checkpoint

CHECKPOINT_VERSION = 2


class Corpus:
    def __init__(
        self,
        max_size: int = 50000,
        novelty_weight: float = 1.0,
        affinity_weight: float = 1.0,
        base_mutations: int = 20,
    ):
        self.max_size = max_size
        self.novelty_weight = novelty_weight
        self.affinity_weight = affinity_weight
        self.base_mutations = base_mutations

        self._entries: dict[str, CorpusEntry] = {}
        self._max_heap: list[tuple[float, int, str]] = []
        self._min_heap: list[tuple[float, int, str]] = []
        self._seq = itertools.count()

    def size(self) -> int:
        return len(self._entries)

    def compute_priority(self, entry: CorpusEntry) -> float:
        return compute_priority(entry, self.novelty_weight, self.affinity_weight)

    def _push(self, entry: CorpusEntry) -> None:
        seq = next(self._seq)
        heapq.heappush(self._max_heap, (-entry.priority, seq, entry.smiles))
        heapq.heappush(self._min_heap, (entry.priority, seq, entry.smiles))

    def add(
        self,
        entry: CorpusEntry | str,
        novelty: int | None = None,
        affinity: float | None = None,
        source_id: str | None = None,
        mutation_type: str | None = None,
    ) -> CorpusEntry:
        recompute_priority = False

        if isinstance(entry, str):
            smiles = canonicalize(entry)
            existing = self._entries.get(smiles)
            if existing is not None:
                incoming = existing
            else:
                incoming = CorpusEntry(smiles=smiles, source_id=source_id or "")
            if novelty is not None:
                incoming.novelty_score = max(incoming.novelty_score, novelty)
            if affinity is not None:
                incoming.best_affinity = (
                    affinity
                    if incoming.best_affinity is None
                    else min(incoming.best_affinity, affinity)
                )
            if mutation_type is not None:
                incoming.mutation_lineage = (incoming.mutation_lineage + [mutation_type])[
                    -10:
                ]
            entry = incoming
            recompute_priority = True
        else:
            entry.smiles = canonicalize(entry.smiles)

        smiles = entry.smiles
        existing = self._entries.get(smiles)

        if existing is not None and existing is not entry:
            existing.best_affinity = _min_optional(existing.best_affinity, entry.best_affinity)
            existing.novelty_score = max(existing.novelty_score, entry.novelty_score)
            existing.finds = max(existing.finds, entry.finds)
            existing.times_fuzzed = max(existing.times_fuzzed, entry.times_fuzzed)
            existing.times_selected = max(existing.times_selected, entry.times_selected)
            existing.favored = existing.favored or entry.favored
            existing.priority = max(existing.priority, entry.priority)
            entry = existing

        if recompute_priority:
            entry.priority = self.compute_priority(entry)

        self._entries[smiles] = entry
        self._push(entry)
        self._trim()
        return entry

    def sample_donor(self, exclude_smiles: str | None = None) -> CorpusEntry | None:
        candidates = [e for s, e in self._entries.items() if s != exclude_smiles]
        if not candidates:
            return None
        return random.choice(candidates)

    def pop(self) -> CorpusEntry | None:
        while self._max_heap:
            neg_priority, _seq, smiles = heapq.heappop(self._max_heap)
            entry = self._entries.get(smiles)
            if entry is None or entry.priority != -neg_priority:
                continue
            entry.times_selected += 1
            return entry
        return None

    def _trim(self) -> None:
        while len(self._entries) > self.max_size:
            evicted = False
            while self._min_heap:
                priority, _seq, smiles = heapq.heappop(self._min_heap)
                entry = self._entries.get(smiles)
                if entry is None or entry.priority != priority:
                    continue
                if entry.favored:
                    continue
                del self._entries[smiles]
                evicted = True
                break
            if not evicted:
                break

    def save(self, path) -> None:
        data = {
            "version": CHECKPOINT_VERSION,
            "max_size": self.max_size,
            "entries": [asdict(e) for e in self._entries.values()],
        }
        save_checkpoint(path, data)

    def load(self, path) -> None:
        data = load_checkpoint(path)
        self.max_size = data.get("max_size", self.max_size)
        self._entries = {}
        self._max_heap = []
        self._min_heap = []
        self._seq = itertools.count()
        for entry_data in data.get("entries", []):
            entry = CorpusEntry(**entry_data)
            self.add(entry)


def _min_optional(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)
