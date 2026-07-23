from __future__ import annotations

import heapq
import itertools
import random
from dataclasses import asdict, fields

from rdkit import Chem, RDLogger
from rdkit.Chem.Scaffolds import MurckoScaffold

from biofuzz.corpus.entry import CorpusEntry
from biofuzz.corpus.scheduler import compute_priority, effective_priority
from biofuzz.corpus.smiles_canon import canonicalize
from biofuzz.storage.checkpoint import load_checkpoint, save_checkpoint

RDLogger.DisableLog("rdApp.*")

CHECKPOINT_VERSION = 3

_ENTRY_FIELDS = {f.name for f in fields(CorpusEntry)}


def murcko_scaffold(smiles: str) -> str:
    """Bemis-Murcko scaffold SMILES, or "" when it can't be derived."""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return ""
        return MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
    except Exception:
        return ""


class Corpus:
    def __init__(
        self,
        max_size: int = 50000,
        novelty_weight: float = 1.0,
        affinity_weight: float = 1.0,
        base_mutations: int = 20,
        scaffold_penalty_weight: float = 3.0,
    ):
        self.max_size = max_size
        self.novelty_weight = novelty_weight
        self.affinity_weight = affinity_weight
        self.base_mutations = base_mutations
        self.scaffold_penalty_weight = scaffold_penalty_weight

        self._entries: dict[str, CorpusEntry] = {}
        self._max_heap: list[tuple[float, int, str]] = []
        self._min_heap: list[tuple[float, int, str]] = []
        self._seq = itertools.count()
        self._scaffold_counts: dict[str, int] = {}
        # Newest heap-record version per molecule. A record is live only if its
        # version matches; anything older is a superseded duplicate. This keeps
        # exactly one live record per entry even though repricing re-pushes.
        self._versions: dict[str, int] = {}

    def size(self) -> int:
        return len(self._entries)

    def scaffold_count(self, scaffold: str) -> int:
        return self._scaffold_counts.get(scaffold, 0)

    def distinct_scaffolds(self) -> int:
        return len(self._scaffold_counts)

    def compute_priority(self, entry: CorpusEntry) -> float:
        """Intrinsic worth from this entry's own evidence (no crowding term)."""
        return compute_priority(entry, self.novelty_weight, self.affinity_weight)

    def effective_priority(self, entry: CorpusEntry) -> float:
        """Queue priority: intrinsic worth discounted by scaffold crowding."""
        base = entry.base_priority if entry.base_priority is not None else entry.priority
        return effective_priority(
            base,
            self._scaffold_counts.get(entry.scaffold, 0),
            self.scaffold_penalty_weight,
        )

    def _push(self, entry: CorpusEntry) -> None:
        version = next(self._seq)
        self._versions[entry.smiles] = version
        entry.priority = self.effective_priority(entry)
        heapq.heappush(self._max_heap, (-entry.priority, version, entry.smiles))
        heapq.heappush(self._min_heap, (entry.priority, version, entry.smiles))

    def _is_live(self, smiles: str, version: int) -> bool:
        return self._versions.get(smiles) == version

    def add(
        self,
        entry: CorpusEntry | str,
        novelty: int | None = None,
        affinity: float | None = None,
        source_id: str | None = None,
        mutation_type: str | None = None,
        rarity: float | None = None,
        ligand_efficiency: float | None = None,
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
            if rarity is not None:
                incoming.rarity = max(incoming.rarity, rarity)
            if ligand_efficiency is not None:
                incoming.ligand_efficiency = (
                    ligand_efficiency
                    if incoming.ligand_efficiency is None
                    else max(incoming.ligand_efficiency, ligand_efficiency)
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
            existing.rarity = max(existing.rarity, entry.rarity)
            existing.ligand_efficiency = _max_optional(
                existing.ligand_efficiency, entry.ligand_efficiency
            )
            existing.finds = max(existing.finds, entry.finds)
            existing.times_fuzzed = max(existing.times_fuzzed, entry.times_fuzzed)
            existing.times_selected = max(existing.times_selected, entry.times_selected)
            existing.favored = existing.favored or entry.favored
            existing.calibrated = existing.calibrated or entry.calibrated
            existing.base_priority = max(
                existing.base_priority or 0.1, entry.base_priority or 0.1
            )
            entry = existing

        is_new = smiles not in self._entries
        if not entry.scaffold:
            entry.scaffold = murcko_scaffold(smiles)
        if is_new:
            self._scaffold_counts[entry.scaffold] = (
                self._scaffold_counts.get(entry.scaffold, 0) + 1
            )

        if recompute_priority:
            entry.base_priority = self.compute_priority(entry)

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
        """Highest-priority entry, repricing crowding lazily.

        An entry's crowding penalty depends on how many *other* entries share
        its scaffold, which keeps growing after it was queued. Without
        repricing, the first analog of a runaway series keeps the priority it
        earned back when its scaffold was still novel and sits at the head of
        the queue indefinitely -- exactly the lock-in the penalty exists to
        prevent. Repricing here, rather than touching every sibling on each
        insert, keeps add() O(log n).

        Terminates: repricing only ever lowers a priority (crowding is
        non-negative and monotonic in the count), and priorities are floored.
        """
        while self._max_heap:
            neg_priority, version, smiles = heapq.heappop(self._max_heap)
            if not self._is_live(smiles, version):
                continue  # superseded by a later push
            entry = self._entries.get(smiles)
            if entry is None:
                continue

            current = self.effective_priority(entry)
            if current < -neg_priority - 1e-9:
                # Its scaffold got crowded since this record was written.
                # Re-push at the correct priority; that bumps the version, so
                # this record is retired rather than duplicated.
                self._push(entry)
                continue

            entry.priority = current
            entry.times_selected += 1
            return entry
        return None

    def _forget(self, smiles: str) -> None:
        entry = self._entries.pop(smiles, None)
        self._versions.pop(smiles, None)
        if entry is None:
            return
        count = self._scaffold_counts.get(entry.scaffold)
        if count is not None:
            if count <= 1:
                self._scaffold_counts.pop(entry.scaffold, None)
            else:
                self._scaffold_counts[entry.scaffold] = count - 1

    def _trim(self) -> None:
        while len(self._entries) > self.max_size:
            evicted = False
            while self._min_heap:
                priority, version, smiles = heapq.heappop(self._min_heap)
                if not self._is_live(smiles, version):
                    continue
                entry = self._entries.get(smiles)
                if entry is None:
                    continue
                if entry.favored:
                    continue
                self._forget(smiles)
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
        self._scaffold_counts = {}
        self._versions = {}
        for entry_data in data.get("entries", []):
            # Tolerate checkpoints written before newer fields existed rather
            # than failing the resume outright.
            known = {k: v for k, v in entry_data.items() if k in _ENTRY_FIELDS}
            self.add(CorpusEntry(**known))


def _min_optional(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _max_optional(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
