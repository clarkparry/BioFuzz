from __future__ import annotations

from pathlib import Path
from typing import Callable

from biofuzz.docking.config import TargetConfig
from biofuzz.docking.parser import parse_log
from biofuzz.docking.runner import DockingResult, dock
from biofuzz.molecules.preparation import prepare_smiles


def _best_affinity(
    smiles: str,
    target_config: TargetConfig,
    ligand_pdbqt: str | None = None,
    exhaustiveness: int = 8,
    num_modes: int = 3,
    engine: str = "gnina",
    dock_observer: Callable[[DockingResult], None] | None = None,
) -> float | None:
    prepared_ligand = ligand_pdbqt or prepare_smiles(smiles)
    if prepared_ligand is None:
        return None

    result = dock(
        prepared_ligand,
        target_config,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        engine=engine,
    )
    if dock_observer is not None:
        dock_observer(result)
    if not result.success:
        return None
    try:
        modes = parse_log(result.log_text)
        if not modes:
            return None
        return modes[0].affinity
    finally:
        if result.pose_path:
            Path(result.pose_path).unlink(missing_ok=True)


def _offtarget_config(target_config: TargetConfig) -> TargetConfig | None:
    if not target_config.offtarget_receptor or not target_config.offtarget_box:
        return None

    return TargetConfig(
        name=f"{target_config.name}_offtarget",
        receptor=target_config.offtarget_receptor,
        box=target_config.offtarget_box,
        pocket=target_config.pocket,
        oracle=target_config.oracle,
    )


def selectivity_ratio(
    smiles: str,
    target_config: TargetConfig,
    offtarget_config: TargetConfig,
) -> float | None:
    return _selectivity_ratio(
        smiles,
        target_config,
        offtarget_config,
    )


def _selectivity_ratio(
    smiles: str,
    target_config: TargetConfig,
    offtarget_config: TargetConfig,
    ligand_pdbqt: str | None = None,
    target_affinity: float | None = None,
    exhaustiveness: int = 8,
    num_modes: int = 3,
    engine: str = "gnina",
    dock_observer: Callable[[DockingResult], None] | None = None,
) -> float | None:
    target_score = target_affinity
    if target_score is None:
        target_score = _best_affinity(
            smiles,
            target_config,
            ligand_pdbqt=ligand_pdbqt,
            exhaustiveness=exhaustiveness,
            num_modes=num_modes,
            engine=engine,
            dock_observer=dock_observer,
        )
    offtarget_affinity = _best_affinity(
        smiles,
        offtarget_config,
        ligand_pdbqt=ligand_pdbqt,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        engine=engine,
        dock_observer=dock_observer,
    )

    if target_score is None or offtarget_affinity is None:
        return None

    if offtarget_affinity == 0:
        return None

    return abs(target_score) / abs(offtarget_affinity)


def selectivity_ratio_from_target(
    smiles: str,
    target_config: TargetConfig,
    ligand_pdbqt: str | None = None,
    target_affinity: float | None = None,
    exhaustiveness: int = 8,
    num_modes: int = 3,
    engine: str = "gnina",
    dock_observer: Callable[[DockingResult], None] | None = None,
) -> float | None:
    offtarget = _offtarget_config(target_config)
    if offtarget is None:
        return None
    return _selectivity_ratio(
        smiles,
        target_config,
        offtarget,
        ligand_pdbqt=ligand_pdbqt,
        target_affinity=target_affinity,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        engine=engine,
        dock_observer=dock_observer,
    )
