from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MutationCandidate:
    smiles: str
    stage: str
    mutation_type: str
    parent_smiles: str
