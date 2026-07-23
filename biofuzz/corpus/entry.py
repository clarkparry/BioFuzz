from __future__ import annotations

from dataclasses import dataclass, field

MAX_LINEAGE_DEPTH = 10


@dataclass
class CorpusEntry:
    smiles: str
    source_id: str = ""
    mutation_lineage: list[str] = field(default_factory=list)
    times_selected: int = 0
    times_fuzzed: int = 0
    best_affinity: float | None = None
    novelty_score: int = 0
    finds: int = 0
    favored: bool = False
    # Effective queue priority: base_priority minus the current scaffold-crowding
    # penalty. Derived and maintained by Corpus; do not set it expecting it to
    # stick once crowding changes.
    priority: float = 0.1
    # Intrinsic worth, independent of how crowded this entry's scaffold is: a
    # seed prior at startup, or an evidence-based score once docked. Kept apart
    # from `priority` so that recomputing the crowding term can never destroy a
    # seed's carefully computed prior. Defaults to `priority` when unset.
    base_priority: float | None = None
    # Bemis-Murcko scaffold, filled in by Corpus.add(). The scheduler uses it to
    # damp lineages that have taken over the queue.
    scaffold: str = ""
    # Mean contact rarity of this molecule's best pose (see CoverageObservation).
    rarity: float = 0.0
    # Ligand efficiency (kcal/mol per heavy atom) of the best pose.
    ligand_efficiency: float | None = None
    # True once the molecule itself has been docked, not just its mutants.
    calibrated: bool = False

    def __post_init__(self) -> None:
        if len(self.mutation_lineage) > MAX_LINEAGE_DEPTH:
            self.mutation_lineage = self.mutation_lineage[-MAX_LINEAGE_DEPTH:]
        if self.base_priority is None:
            self.base_priority = self.priority
