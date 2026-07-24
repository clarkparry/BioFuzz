from __future__ import annotations

import os

from biofuzz.docker import DockingConfig
from biofuzz.docker.gnina import GninaBackend
from biofuzz.docker.parser import parse_log
from biofuzz.oracle import best_mode
from biofuzz.prep import prepare_smiles
from biofuzz.triage.confirmation import DEFAULT_FILTER_KWARGS
from biofuzz.triage.record import TriageRecord, TriageStageResult

# Minimum ddG = offtarget - ontarget, in kcal/mol. 1.4 kcal/mol is one log unit
# of affinity (~10x selectivity), the conventional floor for calling a compound
# selective at all.
DEFAULT_SELECTIVITY_MIN_DDG = 1.4


class SelectivityStage:
    name = "selectivity"

    def __init__(
        self,
        exhaustiveness: int = 16,
        timeout_seconds: int = 900,
        cnn_model: str | None = None,
        scoring_policy: str = "consensus",
    ):
        self.exhaustiveness = exhaustiveness
        self.timeout_seconds = timeout_seconds
        self.cnn_model = cnn_model
        self.scoring_policy = scoring_policy

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
            cnn_model=self.cnn_model,
        )

        backend = GninaBackend()
        result = backend.dock(pdbqt, config)
        if not result.success:
            return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])

        try:
            modes = parse_log(result.log_text)
            if not modes:
                return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])

            _mode, scored = best_mode(modes, self.scoring_policy)
            if scored is None:
                return TriageStageResult(fields={}, flags=["selectivity_skipped_unavailable"])
            offtarget_affinity = scored.score

            # Selectivity is a *difference* of binding free energies, not a
            # ratio of them. Both numbers are kcal/mol on a log scale, so their
            # ratio has no physical meaning -- dividing -10 by -5 to get "2x
            # selective" is a unit error; the real gap is 5 kcal/mol, ~4000x in
            # Kd. ddG > 0 means the compound prefers the on-target.
            ddg = offtarget_affinity - record.confirmed_affinity

            triage_cfg = target_config.get("triage", {})
            min_ddg = triage_cfg.get("selectivity_min_ddg", DEFAULT_SELECTIVITY_MIN_DDG)
            flags = ["selectivity_passed"] if ddg >= min_ddg else ["selectivity_low"]

            return TriageStageResult(
                fields=dict(
                    selectivity_ddg=ddg,
                    offtarget_affinity=offtarget_affinity,
                    # Kd fold-selectivity, the number a chemist actually quotes.
                    selectivity_fold=10 ** (ddg / 1.364),
                ),
                flags=flags,
            )
        finally:
            if result.pose_path and os.path.exists(result.pose_path):
                os.unlink(result.pose_path)
