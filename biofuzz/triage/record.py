from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TriageRecord:
    finding_id: str
    finding_dir: Path
    smiles: str
    initial_affinity: float
    pose_path: Path

    confirmed_affinity: float | None = None
    strain: float | None = None
    ligand_efficiency: float | None = None
    selectivity_ratio: float | None = None
    pose_rmsd_spread: float | None = None
    heavy_atom_count: int | None = None
    molecular_weight: float | None = None
    logp: float | None = None
    tpsa: float | None = None
    rot_bonds: int | None = None
    hbd: int | None = None
    hba: int | None = None

    flags: list[str] = field(default_factory=list)
    filters_failed: list[str] = field(default_factory=list)

    cluster_id: int | None = None
    cluster_rank: int | None = None
    overall_rank: int | None = None

    def to_report_dict(self) -> dict:
        return {
            "smiles": self.smiles,
            "initial_affinity": self.initial_affinity,
            "confirmed_affinity": self.confirmed_affinity,
            "strain": self.strain,
            "ligand_efficiency": self.ligand_efficiency,
            "selectivity_ratio": self.selectivity_ratio,
            "pose_rmsd_spread": self.pose_rmsd_spread,
            "heavy_atom_count": self.heavy_atom_count,
            "molecular_weight": self.molecular_weight,
            "logp": self.logp,
            "tpsa": self.tpsa,
            "rot_bonds": self.rot_bonds,
            "hbd": self.hbd,
            "hba": self.hba,
            "flags": self.flags,
            "filters_failed": self.filters_failed,
            "cluster_id": self.cluster_id,
            "cluster_rank": self.cluster_rank,
            "overall_rank": self.overall_rank,
        }


@dataclass
class TriageStageResult:
    fields: dict
    flags: list[str] = field(default_factory=list)
    filter_failed: str | None = None
