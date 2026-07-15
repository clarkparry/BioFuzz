from __future__ import annotations

from biofuzz.docker import DockingConfig, DockingResult
from biofuzz.docker.gnina import GninaBackend


def dock_worker(ligand_pdbqt: str, config: DockingConfig) -> DockingResult:
    backend = GninaBackend()
    return backend.dock(ligand_pdbqt, config)
