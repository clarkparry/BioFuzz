from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeStatus:
    stage: str
    mutation_stage: str
    mutation_type: str | None = None
    current_parent: str | None = None
    current_smiles: str | None = None
    power_score: float = 0.0
    mutation_budget: int = 0
    total_docks: int = 0
    completed_docks: int = 0
    docks_per_sec: float = 0.0
    completed_dock_staleness_seconds: float | None = None
    corpus_size: int = 0
    hits: int = 0
    coverage_bitmap_occupancy: float = 0.0
    coverage_epoch: int = 0
    novelty_strong: int = 0
    novelty_weak: int = 0
    novelty_none: int = 0
    best_affinity: float | None = None
    checkpoints: int = 0
    elapsed_seconds: float = 0.0
    gpu_active: bool | None = None
    # Fraction of pocket contact bits reached at least once. Unlike bitmap
    # occupancy (which is occupancy of a sparse hash space and stays near zero)
    # this is a directly meaningful "how much of the pocket have we explored".
    union_coverage: float = 0.0
    # Distinct Bemis-Murcko scaffolds in the corpus: the chemical-diversity
    # counterpart to corpus_size. A campaign collapsing onto one series shows
    # up here as a flat line while corpus_size keeps climbing.
    distinct_scaffolds: int = 0
    # Docks avoided because the molecule had already been evaluated.
    docks_skipped: int = 0
