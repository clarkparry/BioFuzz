from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from typing import Sequence

from biofuzz.docking.config import OracleConfig, TargetConfig
from biofuzz.docking.parser import DockingMode
from biofuzz.oracle.selectivity import selectivity_ratio_from_target
from biofuzz.oracle.strain import passes_strain


@dataclass
class OracleVerdict:
    is_hit: bool
    affinity: float
    passed_tiers: list[str]
    notes: str


def passes_affinity(modes: Sequence[DockingMode], threshold: float = -9.0) -> bool:
    return bool(modes) and modes[0].affinity <= threshold


def _oracle_config(config: TargetConfig | OracleConfig) -> OracleConfig:
    if isinstance(config, TargetConfig):
        return config.oracle
    return config


def evaluate(
    modes: Sequence[DockingMode],
    pose_pdbqt: str,
    config: TargetConfig | OracleConfig,
    smiles: str | None = None,
    ligand_pdbqt: str | None = None,
    selectivity_exhaustiveness: int = 8,
    num_modes: int = 3,
    docking_engine: str = "gnina",
    dock_observer: Callable[[], None] | None = None,
) -> OracleVerdict:
    oracle_cfg = _oracle_config(config)
    affinity = modes[0].affinity if modes else float("inf")

    passed_tiers: list[str] = []
    notes: list[str] = []

    affinity_ok = passes_affinity(modes, threshold=oracle_cfg.affinity_threshold)
    if affinity_ok:
        passed_tiers.append("affinity")
    else:
        notes.append(
            f"Affinity {affinity:.2f} did not reach threshold {oracle_cfg.affinity_threshold:.2f}"
        )

    strain_ok = passes_strain(pose_pdbqt, threshold=oracle_cfg.strain_threshold)
    if strain_ok:
        passed_tiers.append("strain")
    else:
        notes.append(f"Strain exceeded threshold {oracle_cfg.strain_threshold:.2f}")

    selectivity_ok = True
    # Selectivity is expensive and only meaningful once affinity has already passed.
    if (
        affinity_ok
        and isinstance(config, TargetConfig)
        and smiles
        and config.offtarget_receptor
        and config.offtarget_box
    ):
        ratio = selectivity_ratio_from_target(
            smiles,
            config,
            ligand_pdbqt=ligand_pdbqt,
            target_affinity=affinity,
            exhaustiveness=selectivity_exhaustiveness,
            num_modes=num_modes,
            engine=docking_engine,
            dock_observer=dock_observer,
        )
        if ratio is None:
            notes.append("Selectivity skipped: target/off-target docking unavailable")
        elif ratio >= oracle_cfg.selectivity_ratio_min:
            passed_tiers.append("selectivity")
        else:
            selectivity_ok = False
            notes.append(
                "Selectivity ratio below threshold "
                f"({ratio:.2f} < {oracle_cfg.selectivity_ratio_min:.2f})"
            )

    is_hit = affinity_ok and strain_ok and selectivity_ok

    return OracleVerdict(
        is_hit=is_hit,
        affinity=affinity,
        passed_tiers=passed_tiers,
        notes="; ".join(notes) if notes else "passed",
    )
