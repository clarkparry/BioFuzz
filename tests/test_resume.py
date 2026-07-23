import json

import pytest

from biofuzz.storage import FindingsStore, make_run_dir, open_run_dir


def _save(store, tmp_path, smiles, affinity=-10.0, **kwargs):
    pose = tmp_path / "pose.pdbqt"
    pose.write_text("MODEL 1\nENDMDL\n")
    return store.save(
        smiles=smiles,
        verdict={"passed_tiers": ["affinity"], "notes": "passed"},
        pose_path=pose,
        affinity=affinity,
        **kwargs,
    )


def test_duplicate_finding_is_suppressed(tmp_path):
    """The same molecule rediscovered twice must be saved once.

    The reference campaign saved one molecule under two finding IDs, which
    double-counted the hit and made triage re-dock it.
    """
    store = FindingsStore(tmp_path / "findings")
    smiles = "CCOc1ccccc1"

    first = _save(store, tmp_path, smiles, affinity=-10.24)
    assert first is not None

    second = _save(store, tmp_path, smiles, affinity=-10.25)
    assert second is None
    assert store.already_saved(smiles)
    assert len(list((tmp_path / "findings").glob("*/metadata.json"))) == 1


def test_distinct_molecules_both_saved(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    assert _save(store, tmp_path, "CCOc1ccccc1") is not None
    assert _save(store, tmp_path, "CCOc1ccccc1C") is not None
    assert len(list((tmp_path / "findings").glob("*/metadata.json"))) == 2


def test_dedupe_can_be_disabled(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    _save(store, tmp_path, "CCO")
    assert _save(store, tmp_path, "CCO", dedupe=False) is not None


def test_findings_store_reindexes_existing_findings_on_resume(tmp_path):
    """A resumed run must not re-save findings already on disk."""
    findings_dir = tmp_path / "findings"
    store = FindingsStore(findings_dir)
    _save(store, tmp_path, "CCOc1ccccc1")

    reopened = FindingsStore(findings_dir)
    assert reopened.already_saved("CCOc1ccccc1")
    assert _save(reopened, tmp_path, "CCOc1ccccc1") is None


def test_finding_metadata_records_scoring_detail(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    finding_dir = _save(
        store,
        tmp_path,
        "CCO",
        affinity=-10.5,
        ligand_efficiency=0.31,
        vina_affinity=-10.5,
        cnn_affinity_kcal=-11.2,
        cnn_pose_score=0.87,
        scoring_policy="consensus",
        heavy_atom_count=34,
    )
    meta = json.loads((finding_dir / "metadata.json").read_text())
    assert meta["ligand_efficiency"] == 0.31
    assert meta["cnn_pose_score"] == 0.87
    assert meta["scoring_policy"] == "consensus"


def test_open_run_dir_reattaches_instead_of_allocating(tmp_path):
    """--resume must reuse the run directory, not create a suffixed sibling."""
    layout = make_run_dir(tmp_path, "hiv_protease")

    # make_run_dir refuses to reuse, by design: a second call sidesteps.
    fresh = make_run_dir(tmp_path, "hiv_protease")
    assert fresh.root != layout.root

    reopened = open_run_dir(layout.root)
    assert reopened.root == layout.root
    assert reopened.corpus_dir == layout.corpus_dir
    assert reopened.coverage_path == layout.coverage_path


def test_open_run_dir_rejects_missing_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_run_dir(tmp_path / "no-such-run")
