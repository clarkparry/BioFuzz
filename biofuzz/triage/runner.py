from __future__ import annotations

from dataclasses import fields

from biofuzz.triage.admet import ADMETStage
from biofuzz.triage.chemistry_flags import ChemistryFlagsStage
from biofuzz.triage.clustering import cluster_records
from biofuzz.triage.confirmation import ConfirmationDockingStage
from biofuzz.triage.loader import load_findings
from biofuzz.triage.pose_quality import LigandEfficiencyStage, PoseQualityStage
from biofuzz.triage.record import TriageRecord
from biofuzz.triage.selectivity import SelectivityStage

_RECORD_FIELD_NAMES = {f.name for f in fields(TriageRecord)}


def default_stages(
    exhaustiveness_confirm: int = 16,
    cnn_model_confirm: str | None = None,
    scoring_policy: str = "consensus",
) -> list:
    return [
        ConfirmationDockingStage(
            exhaustiveness=exhaustiveness_confirm,
            cnn_model=cnn_model_confirm,
            scoring_policy=scoring_policy,
        ),
        PoseQualityStage(),
        ADMETStage(),  # must run before LigandEfficiencyStage: it computes heavy_atom_count
        LigandEfficiencyStage(),
        ChemistryFlagsStage(),
        SelectivityStage(
            exhaustiveness=exhaustiveness_confirm,
            cnn_model=cnn_model_confirm,
            scoring_policy=scoring_policy,
        ),
    ]


def run_triage(
    findings_dir: str,
    target_config: dict,
    receptor_path: str,
    stages: list | None = None,
) -> list[TriageRecord]:
    records = load_findings(findings_dir)
    stages = stages if stages is not None else default_stages()

    for record in records:
        for stage in stages:
            result = stage.analyze(record, target_config, receptor_path=receptor_path)
            for key, value in result.fields.items():
                if key in _RECORD_FIELD_NAMES:
                    setattr(record, key, value)
            record.flags.extend(result.flags)
            if result.filter_failed:
                record.filters_failed.append(result.filter_failed)

    cluster_records(records)
    _assign_overall_rank(records)

    return records


def _score(record: TriageRecord) -> float:
    """Ranking score: binding strength weighted by ligand efficiency.

    Both terms are taken with their sign intact, so a molecule with an
    unfavourable (positive) confirmed score cannot rank above a real binder.
    Findings that failed a filter sort to the bottom regardless.
    """
    if record.filters_failed:
        return float("-inf")
    confirmed = record.confirmed_affinity if record.confirmed_affinity is not None else 0.0
    le = record.ligand_efficiency if record.ligand_efficiency is not None else 0.0
    return max(0.0, -confirmed) * le


def _assign_overall_rank(records: list[TriageRecord]) -> None:
    ranked = sorted(records, key=_score, reverse=True)
    for rank, record in enumerate(ranked, start=1):
        record.overall_rank = rank
