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
    priority: float = 0.1

    def __post_init__(self) -> None:
        if len(self.mutation_lineage) > MAX_LINEAGE_DEPTH:
            self.mutation_lineage = self.mutation_lineage[-MAX_LINEAGE_DEPTH:]
