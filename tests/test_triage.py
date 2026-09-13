import json
import os
from pathlib import Path

import pytest

from biofuzz.triage.admet import ADMETStage
from biofuzz.triage.chemistry_flags import ChemistryFlagsStage
from biofuzz.triage.clustering import cluster_records
from biofuzz.triage.loader import load_findings
from biofuzz.triage.pose_quality import LigandEfficiencyStage, PoseQualityStage
from biofuzz.triage.record import TriageRecord
from biofuzz.triage.report import write_report


def _make_finding(tmp_path, smiles, affinity, name="000001_20250115T142301_-10.50"):
    finding_dir = tmp_path / "findings" / name
    finding_dir.mkdir(parents=True)
    (finding_dir / "pose.pdbqt").write_text(
        "MODEL 1\n"
        "ATOM      1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
        "ATOM      2  C2  LIG A   1       1.500   0.000   0.000  1.00  0.00     0.000 C\n"
        "ENDMDL\n"
    )
    (finding_dir / "metadata.json").write_text(
        json.dumps({"smiles": smiles, "affinity": affinity, "passed_tiers": ["affinity"]})
    )
    return finding_dir


def test_load_findings(tmp_path):
    _make_finding(tmp_path, "CC(=O)Oc1ccccc1C(=O)O", -10.5)
    records = load_findings(tmp_path / "findings")
    assert len(records) == 1
    assert records[0].smiles == "CC(=O)Oc1ccccc1C(=O)O"
    assert records[0].initial_affinity == -10.5


def test_admet_stage_flags_lipinski_violations():
    stage = ADMETStage()
    record = TriageRecord(
        finding_id="1", finding_dir=None, smiles="CC(=O)Oc1ccccc1C(=O)O",
        initial_affinity=-10.0, pose_path=None,
    )
    result = stage.analyze(record, target_config={})
    assert result.fields["molecular_weight"] > 0
    assert "lipinski_mw_violation" not in result.flags  # aspirin is well within bounds


def test_admet_stage_flags_large_molecule():
    stage = ADMETStage()
    c40_alkane = "C" * 40
    record = TriageRecord(
        finding_id="1", finding_dir=None, smiles=c40_alkane, initial_affinity=-10.0, pose_path=None
    )
    result = stage.analyze(record, target_config={})
    assert "lipinski_mw_violation" in result.flags


def test_chemistry_flags_stage_detects_aldehyde():
    stage = ChemistryFlagsStage()
    record = TriageRecord(
        finding_id="1", finding_dir=None, smiles="CCC=O", initial_affinity=-10.0, pose_path=None
    )
    result = stage.analyze(record, target_config={})
    assert any("aldehyde" in f for f in result.flags)


def test_chemistry_flags_stage_clean_molecule_no_reactive_flags():
    stage = ChemistryFlagsStage()
    record = TriageRecord(
        finding_id="1", finding_dir=None, smiles="CC(=O)Oc1ccccc1C(=O)O",
        initial_affinity=-10.0, pose_path=None,
    )
    result = stage.analyze(record, target_config={})
    assert not any(f.startswith("reactive_group") for f in result.flags)


def test_pose_quality_stage_flags_high_strain():
    stage = PoseQualityStage(strain_threshold=3.5)
    record = TriageRecord(
        finding_id="1", finding_dir=None, smiles="CCO", initial_affinity=-10.0, pose_path=None,
        strain=8.2,
    )
    result = stage.analyze(record, target_config={})
    assert result.filter_failed == "high_strain"


def test_ligand_efficiency_computed_correctly():
    stage = LigandEfficiencyStage(le_threshold=0.3)
    record = TriageRecord(
        finding_id="1", finding_dir=None, smiles="CCO", initial_affinity=-10.0, pose_path=None,
        confirmed_affinity=-10.0, heavy_atom_count=20,
    )
    result = stage.analyze(record, target_config={})
    assert result.fields["ligand_efficiency"] == 0.5
    assert "low_ligand_efficiency" not in result.flags


def test_clustering_groups_nearby_poses(tmp_path):
    dir1 = _make_finding(tmp_path, "CCO", -10.0, name="a")
    dir2 = _make_finding(tmp_path, "CCC", -9.0, name="b")

    r1 = TriageRecord(
        finding_id="a", finding_dir=dir1, smiles="CCO", initial_affinity=-10.0,
        pose_path=dir1 / "pose.pdbqt", ligand_efficiency=0.5,
    )
    r2 = TriageRecord(
        finding_id="b", finding_dir=dir2, smiles="CCC", initial_affinity=-9.0,
        pose_path=dir2 / "pose.pdbqt", ligand_efficiency=0.3,
    )
    cluster_records([r1, r2])
    assert r1.cluster_id == r2.cluster_id  # same coordinates in fixture -> same cluster
    assert r1.cluster_rank == 1  # higher LE ranked first


def test_admet_stage_runs_before_ligand_efficiency_in_default_stages():
    # Regression test: LigandEfficiencyStage needs heavy_atom_count, which
    # only ADMETStage computes -- an earlier ordering bug ran LE first,
    # silently leaving ligand_efficiency null for every real report.
    from biofuzz.triage.runner import default_stages

    stages = default_stages()
    stage_names = [s.name for s in stages]
    assert stage_names.index("admet") < stage_names.index("ligand_efficiency")


def test_write_report_creates_expected_files(tmp_path):
    dir1 = _make_finding(tmp_path, "CCO", -10.0, name="a")
    record = TriageRecord(
        finding_id="a", finding_dir=dir1, smiles="CCO", initial_affinity=-10.0,
        pose_path=dir1 / "pose.pdbqt", confirmed_affinity=-10.1, ligand_efficiency=0.42,
        overall_rank=1,
    )
    output_dir = tmp_path / "triage"
    report_path = write_report([record], output_dir, top_n=5)

    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert len(data) == 1
    assert data[0]["confirmed_affinity"] == -10.1

    assert (output_dir / "report.html").exists()
    top_hits = list((output_dir / "top_hits").iterdir())
    assert len(top_hits) == 1
    assert (top_hits[0] / "pose.pdbqt").exists()
    assert (top_hits[0] / "metadata.json").exists()
    assert (top_hits[0] / "summary.txt").exists()


def test_ligand_efficiency_uses_the_oracle_definition():
    """Triage must report the same quantity the in-loop tier gated on."""
    from biofuzz.oracle.scoring import ligand_efficiency
    from biofuzz.triage.pose_quality import LigandEfficiencyStage

    record = TriageRecord(
        finding_id="000001",
        finding_dir=Path("."),
        smiles="CCO",
        initial_affinity=-10.0,
        pose_path=Path("pose.pdbqt"),
        confirmed_affinity=-10.0,
        heavy_atom_count=25,
    )
    result = LigandEfficiencyStage().analyze(record, {})
    assert result.fields["ligand_efficiency"] == ligand_efficiency(-10.0, 25)
    assert result.fields["ligand_efficiency"] == pytest.approx(0.4)
    assert result.flags == []


def test_unfavourable_score_does_not_produce_a_good_ligand_efficiency():
    """A positive (unfavourable) score must not be flipped into a strong LE.

    Taking abs() of the affinity would make a molecule that docks badly look
    maximally efficient, and the ranking would promote it.
    """
    from biofuzz.triage.pose_quality import LigandEfficiencyStage

    record = TriageRecord(
        finding_id="000002",
        finding_dir=Path("."),
        smiles="CCO",
        initial_affinity=+8.0,
        pose_path=Path("pose.pdbqt"),
        confirmed_affinity=+8.0,
        heavy_atom_count=10,
    )
    result = LigandEfficiencyStage().analyze(record, {})
    assert result.fields["ligand_efficiency"] < 0
    assert "low_ligand_efficiency" in result.flags


def test_ranking_never_promotes_an_unfavourable_score():
    """Rank must respect the sign of the binding energy."""
    from biofuzz.triage.runner import _score

    good = TriageRecord(
        finding_id="1", finding_dir=Path("."), smiles="CCO",
        initial_affinity=-11.0, pose_path=Path("p"),
        confirmed_affinity=-11.0, ligand_efficiency=0.35,
    )
    unfavourable = TriageRecord(
        finding_id="2", finding_dir=Path("."), smiles="CCC",
        initial_affinity=9.0, pose_path=Path("p"),
        confirmed_affinity=9.0, ligand_efficiency=0.9,
    )
    assert _score(good) > _score(unfavourable)
    assert _score(unfavourable) == 0.0
