import re

from biofuzz.storage import (
    CheckpointVersionError,
    FindingsStore,
    FuzzerLog,
    PDBQTCache,
    load_checkpoint,
    load_coverage_checkpoint,
    make_run_dir,
    save_checkpoint,
)
import pytest


def _make_pose_file(tmp_path, name="pose_src.pdbqt"):
    pose = tmp_path / name
    pose.write_text("ROOT\nATOM      1  C1  LIG A   1       0.000   0.000   0.000\nENDROOT\nTORSDOF 0\n")
    return pose


def test_findings_sequential_ids_no_gaps(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    pose = _make_pose_file(tmp_path)

    path1 = store.save(smiles="c1ccccc1", verdict={}, pose_path=pose, affinity=-10.5)
    path2 = store.save(smiles="CCO", verdict={}, pose_path=pose, affinity=-9.0)

    id1 = int(path1.name.split("_")[0])
    id2 = int(path2.name.split("_")[0])
    assert id2 == id1 + 1


def test_findings_creates_pose_and_metadata(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    pose = _make_pose_file(tmp_path)

    path = store.save(
        smiles="c1ccccc1",
        verdict={"passed_tiers": ["affinity"]},
        pose_path=pose,
        affinity=-10.5,
    )

    assert path.exists()
    assert (path / "pose.pdbqt").exists()
    assert (path / "metadata.json").exists()

    import json

    metadata = json.loads((path / "metadata.json").read_text())
    assert metadata["smiles"] == "c1ccccc1"
    assert metadata["affinity"] == -10.5
    assert metadata["passed_tiers"] == ["affinity"]


def test_findings_dir_name_pattern(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    pose = _make_pose_file(tmp_path)
    path = store.save(smiles="CCO", verdict={}, pose_path=pose, affinity=-10.5)
    assert re.match(r"^\d{6}_\d{8}T\d{6}_-?\d+\.\d{2}$", path.name)


def test_findings_counter_persists_across_instances(tmp_path):
    root = tmp_path / "findings"
    pose = _make_pose_file(tmp_path)

    store1 = FindingsStore(root)
    path1 = store1.save(smiles="CCO", verdict={}, pose_path=pose, affinity=-9.0)

    store2 = FindingsStore(root)
    path2 = store2.save(smiles="CCC", verdict={}, pose_path=pose, affinity=-9.5)

    id1 = int(path1.name.split("_")[0])
    id2 = int(path2.name.split("_")[0])
    assert id2 == id1 + 1


def test_findings_save_finding_alias(tmp_path):
    store = FindingsStore(tmp_path / "findings")
    pose = _make_pose_file(tmp_path)
    path = store.save_finding(smiles="CCO", verdict={}, pose_path=pose, affinity=-9.0)
    assert path.exists()


def test_cache_basic_roundtrip(tmp_path):
    cache = PDBQTCache(tmp_path / "cache")
    cache.set("CCO", "PDBQT content A")
    assert cache.get("CCO") == "PDBQT content A"
    assert cache.get("CCC") is None


def test_cache_lru_eviction_falls_back_to_disk(tmp_path):
    cache = PDBQTCache(tmp_path / "cache", max_memory_entries=3)
    cache.set("CCO", "A")
    cache.set("CCC", "B")
    cache.set("CCCC", "C")
    cache.set("CCCCC", "D")  # triggers eviction of "CCO" (oldest)

    assert cache._key("CCO") not in cache._memory
    result = cache.get("CCO")
    assert result == "A"  # retrieved from disk


def test_paths_make_run_dir_creates_structure(tmp_path):
    layout = make_run_dir(tmp_path, "hiv_protease", stamp="2025-01-15")
    assert layout.corpus_dir.exists()
    assert layout.findings_dir.exists()
    assert layout.cache_dir.exists()
    assert layout.root.name == "2025-01-15_hiv_protease"


def test_paths_make_run_dir_counter_suffix(tmp_path):
    layout1 = make_run_dir(tmp_path, "hiv_protease", stamp="2025-01-15")
    layout2 = make_run_dir(tmp_path, "hiv_protease", stamp="2025-01-15")
    layout3 = make_run_dir(tmp_path, "hiv_protease", stamp="2025-01-15")
    assert layout1.root.name == "2025-01-15_hiv_protease"
    assert layout2.root.name == "2025-01-15_hiv_protease_2"
    assert layout3.root.name == "2025-01-15_hiv_protease_3"


def test_fuzzer_log_writes_and_persists(tmp_path):
    log_path = tmp_path / "fuzzer.log"
    log = FuzzerLog(log_path)
    log.write("INFO", "adding 142 seeds to corpus")
    log.write("HIT", "c1ccc(O)cc1 | affinity=-10.50")
    log.close()

    content = log_path.read_text()
    lines = content.strip().splitlines()
    assert len(lines) == 2
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[INFO\] adding 142 seeds to corpus$", lines[0])
    assert "[HIT]" in lines[1]


def test_checkpoint_roundtrip(tmp_path):
    path = tmp_path / "checkpoint.json"
    save_checkpoint(path, {"version": 2, "entries": []})
    data = load_checkpoint(path)
    assert data == {"version": 2, "entries": []}


def test_coverage_checkpoint_version_mismatch_rejected(tmp_path):
    path = tmp_path / "coverage.json"
    save_checkpoint(path, {"version": 2})
    with pytest.raises(CheckpointVersionError):
        load_coverage_checkpoint(path, expected_version=3)


def test_coverage_checkpoint_version_match_loads(tmp_path):
    path = tmp_path / "coverage.json"
    save_checkpoint(path, {"version": 3, "epoch": 1})
    data = load_coverage_checkpoint(path, expected_version=3)
    assert data["epoch"] == 1
