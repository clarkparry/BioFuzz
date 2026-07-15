from __future__ import annotations

import os

from biofuzz.docker import DockingConfig
from biofuzz.docker.gnina import GninaBackend
from biofuzz.docker.parser import parse_log
from biofuzz.prep import prepare_smiles
from biofuzz.triage.confirmation import DEFAULT_FILTER_KWARGS
from biofuzz.triage.record import TriageRecord, TriageStageResult

DEFAULT_SELECTIVITY_RATIO_MIN = 2.0


class SelectivityStage:
    name = "selectivity"

    def __init__(self, exhaustiveness: int = 16, timeout_seconds: int = 300):
        self.exhaustiveness = exhaustiveness
        self.timeout_seconds = timeout_seconds

    def analyze(self, record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult:
        offtarget_receptor = target_config.get("offtarget_receptor")
        offtarget_box = target_config.get("offtarget_box")
        if not offtarget_receptor or not offtarget_box:
            return TriageStageResult(fields={}, flags=["selectivity_not_configured"])

        if record.confirmed_affinity is None:
            return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])

        pdbqt = prepare_smiles(record.smiles, **DEFAULT_FILTER_KWARGS)
        if pdbqt is None:
            return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])

        config = DockingConfig(
            receptor_path=offtarget_receptor,
            center_x=offtarget_box["center_x"], center_y=offtarget_box["center_y"],
            center_z=offtarget_box["center_z"],
            size_x=offtarget_box["size_x"], size_y=offtarget_box["size_y"],
            size_z=offtarget_box["size_z"],
            exhaustiveness=self.exhaustiveness,
            num_modes=3,
            timeout_seconds=self.timeout_seconds,
        )

        backend = GninaBackend()
        result = backend.dock(pdbqt, config)
        if not result.success:
            return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])

        try:
            modes = parse_log(result.log_text)
            if not modes or modes[0].affinity == 0:
                return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])

            ratio = abs(record.confirmed_affinity) / abs(modes[0].affinity)
            ratio_min = target_config.get("triage", {}).get(
                "selectivity_ratio_min", DEFAULT_SELECTIVITY_RATIO_MIN
            )
            flags = ["selectivity_passed"] if ratio >= ratio_min else ["selectivity_low"]

            return TriageStageResult(fields=dict(selectivity_ratio=ratio), flags=flags)
        finally:
            if result.pose_path and os.path.exists(result.pose_path):
                os.unlink(result.pose_path)
