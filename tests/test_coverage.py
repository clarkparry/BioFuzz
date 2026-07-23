import os
import tempfile

from biofuzz.coverage import CoverageMap


def test_novelty_classification_and_dedup():
    cov = CoverageMap(
        pocket_residue_ids={"A:25", "A:27", "A:50"},
        map_size_bytes=1024,
        occupancy_rotate_threshold=0.8,
    )

    fp1 = frozenset({"A:25", "A:27"})
    fp2 = frozenset({"A:25", "A:50"})  # different fingerprint
    fp3 = frozenset({"A:25", "A:27"})  # same as fp1

    obs1 = cov.observe(fp1)
    assert obs1.novelty_class == "strong"
    assert obs1.novelty_score == 2

    obs2 = cov.observe(fp2)
    assert obs2.novelty_class == "strong"  # different slot
    assert obs2.hash_slot != obs1.hash_slot

    obs3 = cov.observe(fp3)
    assert obs3.novelty_class == "none"  # fp1's slot already in current


def test_epoch_rotation_and_weak_novelty():
    # Doc's own build-criterion numbers (map_size_bytes=1024,
    # occupancy_rotate_threshold=0.01) can never trigger rotation from a
    # single observation (1/1024 ~= 0.098% < 1%) -- using a small map and a
    # threshold reachable by one hit instead, same semantics.
    cov = CoverageMap(
        pocket_residue_ids={"A:25", "A:27", "A:50"},
        map_size_bytes=8,
        occupancy_rotate_threshold=0.1,
    )
    fp1 = frozenset({"A:25", "A:27"})

    obs = cov.observe(fp1)  # 1/8 = 12.5% >= 10% -> triggers rotation
    assert obs.rotated
    assert cov.epoch == 1

    obs2 = cov.observe(fp1)
    assert obs2.novelty_class == "weak"  # fp1 in previous, not current


def test_checkpoint_roundtrip():
    cov = CoverageMap(
        pocket_residue_ids={"A:25", "A:27", "A:50"},
        map_size_bytes=1024,
    )
    cov.observe(frozenset({"A:25", "A:27"}))
    cov.observe(frozenset({"A:25", "A:50"}))

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        cov.save(path)
        cov_loaded = CoverageMap(
            pocket_residue_ids={"A:25", "A:27", "A:50"}, map_size_bytes=1024
        )
        cov_loaded.load(path)
        assert cov_loaded.epoch == cov.epoch
        assert cov_loaded.strong_novelty_count == cov.strong_novelty_count
    finally:
        os.unlink(path)


def test_checkpoint_version_mismatch_rejected():
    import json

    cov = CoverageMap(pocket_residue_ids={"A:25"}, map_size_bytes=1024)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        cov.save(path)
        data = json.loads(open(path).read())
        data["version"] = 1
        with open(path, "w") as fh:
            json.dump(data, fh)

        from biofuzz.storage.checkpoint import CheckpointVersionError

        cov2 = CoverageMap(pocket_residue_ids={"A:25"}, map_size_bytes=1024)
        try:
            cov2.load(path)
            assert False, "expected CheckpointVersionError"
        except CheckpointVersionError:
            pass
    finally:
        os.unlink(path)


def test_map_size_must_be_power_of_two():
    import pytest

    with pytest.raises(ValueError):
        CoverageMap(pocket_residue_ids={"A:25"}, map_size_bytes=1000)


def test_bucketed_hit_frequency_grows_with_repeats():
    cov = CoverageMap(
        pocket_residue_ids={"A:25", "A:27"},
        map_size_bytes=1024,
        occupancy_rotate_threshold=1.0,
    )
    fp = frozenset({"A:25"})
    obs = None
    for _ in range(5):
        obs = cov.observe(fp)
    # 5 cumulative hits on the same slot -> bucket 4 (4-7 range)
    assert obs.bitmap_slot_byte == 4


def test_interaction_types_extend_fingerprint_bit_space():
    cov_plain = CoverageMap(pocket_residue_ids={"A:25"}, map_size_bytes=1024)
    cov_typed = CoverageMap(
        pocket_residue_ids={"A:25"}, map_size_bytes=1024, interaction_types_enabled=True
    )
    assert len(cov_plain.residue_to_bit_index) == 1
    assert len(cov_typed.residue_to_bit_index) == 4


def test_observe_with_real_pose_and_receptor_contacts():
    from biofuzz.docker import DockingConfig
    from biofuzz.docker.gnina import GninaBackend
    from biofuzz.docker.parser import parse_pose
    from biofuzz.protein import parse_receptor_residues

    pocket_residue_ids = {
        "A:25", "A:27", "A:28", "A:29", "A:30", "A:32", "A:48", "A:49", "A:50",
        "B:25", "B:27", "B:28", "B:29", "B:30", "B:32", "B:48", "B:49", "B:50",
    }

    backend = GninaBackend()
    config = DockingConfig(
        receptor_path="targets/hiv_protease/protein.pdbqt",
        center_x=13.073, center_y=22.467, center_z=5.557,
        size_x=20.0, size_y=20.0, size_z=20.0,
        exhaustiveness=4, num_modes=1, timeout_seconds=300,
        # Without cnn_model this selects gnina's full model ensemble (~97s per
        # dock on an idle CPU here), which flakes against a short timeout under
        # parallel test load. "fast" is what the fuzzer actually runs.
        cnn_model="fast",
    )
    ligand_pdbqt = open("targets/hiv_protease/reference_ligands/indinavir.pdbqt").read()
    result = backend.dock(ligand_pdbqt, config)
    assert result.success

    try:
        pose_atoms = parse_pose(open(result.pose_path).read())
        protein_residues = parse_receptor_residues(
            "targets/hiv_protease/protein.pdbqt", residue_ids=pocket_residue_ids
        )

        cov = CoverageMap(pocket_residue_ids=pocket_residue_ids, map_size_bytes=4096)
        obs = cov.observe(pose_atoms, protein_residues, smiles="indinavir")

        assert len(obs.fingerprint) > 0  # indinavir should contact the active site
        assert obs.novelty_class == "strong"
        assert cov.pioneer_for(obs.hash_slot) == "indinavir"
    finally:
        os.unlink(result.pose_path)
