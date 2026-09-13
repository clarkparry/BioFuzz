from __future__ import annotations

from biofuzz.docker import DockingConfig, DockingResult, get_backend
from biofuzz.prep import prepare_smiles


def dock_worker(ligand_pdbqt: str, config: DockingConfig, engine: str = "gnina") -> DockingResult:
    """Dock one ligand in a worker process.

    The engine is resolved by name through the backend registry rather than
    imported directly, so `docking.engine` in config.yaml selects the
    implementation and the fuzzer depends only on the DockingBackend contract.
    """
    return get_backend(engine).dock(ligand_pdbqt, config)


def prepare_worker(smiles: str, filter_kwargs: dict) -> str | None:
    """SMILES -> PDBQT in a worker process.

    Conformer embedding and MMFF optimisation are pure CPU and independent per
    molecule, so they parallelise over the same pool the docks use instead of
    running serially on the main process while the workers sit idle.
    """
    return prepare_smiles(smiles, **filter_kwargs)
