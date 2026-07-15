from __future__ import annotations

from biofuzz.triage.record import TriageRecord, TriageStageResult


class PoseQualityStage:
    name = "pose_quality"

    def __init__(self, strain_threshold: float = 3.5, rmsd_flag_threshold: float = 2.0):
        self.strain_threshold = strain_threshold
        self.rmsd_flag_threshold = rmsd_flag_threshold

    def analyze(self, record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult:
        flags = []
        filter_failed = None

        if record.strain is not None and record.strain > self.strain_threshold:
            filter_failed = "high_strain"

        if record.pose_rmsd_spread is not None and record.pose_rmsd_spread > self.rmsd_flag_threshold:
            flags.append("pose_not_well_defined")

        return TriageStageResult(fields={}, flags=flags, filter_failed=filter_failed)


class LigandEfficiencyStage:
    name = "ligand_efficiency"

    def __init__(self, le_threshold: float = 0.3):
        self.le_threshold = le_threshold

    def analyze(self, record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult:
        affinity = record.confirmed_affinity if record.confirmed_affinity is not None else record.initial_affinity
        if not record.heavy_atom_count:
            return TriageStageResult(fields={})

        le = abs(affinity) / record.heavy_atom_count
        flags = []
        if le < self.le_threshold:
            flags.append("low_ligand_efficiency")

        return TriageStageResult(fields=dict(ligand_efficiency=le), flags=flags)
