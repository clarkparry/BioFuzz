from __future__ import annotations

import os

from biofuzz.docker import DockingConfig
from biofuzz.docker.gnina import GninaBackend
from biofuzz.docker.parser import parse_all_poses, parse_log
from biofuzz.oracle import extract_strain
from biofuzz.prep import prepare_smiles
from biofuzz.triage.record import TriageRecord, TriageStageResult

# A finding already passed the fuzzer's drug-likeness gate once to become
# a finding in the first place -- re-applying that same gate here would be
# redundant and can wrongly reject legitimate hits if the campaign that
# produced them used looser bounds than these. Kept permissive; ADMETStage
# is where drug-likeness gets (re-)reported for the triage report itself.
DEFAULT_FILTER_KWARGS = dict(
    min_mw=0.0, max_mw=2000.0, max_logp=10.0, max_hbd=20, max_hba=20, max_rot_bonds=30
)


def _rmsd(atoms_a, atoms_b) -> float | None:
    if len(atoms_a) != len(atoms_b) or not atoms_a:
        return None
    total = 0.0
    for a, b in zip(atoms_a, atoms_b):
        total += (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2
    return (total / len(atoms_a)) ** 0.5


class ConfirmationDockingStage:
    name = "confirmation_docking"

    def __init__(
        self,
        exhaustiveness: int = 16,
        timeout_seconds: int = 300,
        cnn_model: str | None = None,
    ):
        self.exhaustiveness = exhaustiveness
        self.timeout_seconds = timeout_seconds
        self.cnn_model = cnn_model

    def analyze(self, record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult:
        pdbqt = prepare_smiles(record.smiles, **DEFAULT_FILTER_KWARGS)
        if pdbqt is None:
            return TriageStageResult(fields={}, filter_failed="reprep_failed")

        box = target_config["box"]
        config = DockingConfig(
            receptor_path=kwargs["receptor_path"],
            center_x=box["center_x"], center_y=box["center_y"], center_z=box["center_z"],
            size_x=box["size_x"], size_y=box["size_y"], size_z=box["size_z"],
            exhaustiveness=self.exhaustiveness,
            num_modes=3,
            timeout_seconds=self.timeout_seconds,
            cnn_model=self.cnn_model,
        )

        backend = GninaBackend()
        result = backend.dock(pdbqt, config)
        if not result.success:
            return TriageStageResult(fields={}, filter_failed="confirmation_dock_failed")

        try:
            modes = parse_log(result.log_text)
            if not modes:
                return TriageStageResult(fields={}, filter_failed="confirmation_dock_failed")

            confirmed_affinity = modes[0].affinity

            pose_text = open(result.pose_path).read()
            poses = parse_all_poses(pose_text)
            rmsd_spread = _rmsd(poses[0], poses[1]) if len(poses) > 1 else 0.0
            strain = extract_strain(pose_text)

            filter_failed = None
            if abs(confirmed_affinity - record.initial_affinity) > 1.5:
                filter_failed = "confirmation_affinity_mismatch"

            return TriageStageResult(
                fields=dict(
                    confirmed_affinity=confirmed_affinity,
                    pose_rmsd_spread=rmsd_spread,
                    strain=strain,
                ),
                filter_failed=filter_failed,
            )
        finally:
            if result.pose_path and os.path.exists(result.pose_path):
                os.unlink(result.pose_path)
