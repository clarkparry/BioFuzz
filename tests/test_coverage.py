from pathlib import Path
import json

import pytest

from biofuzz.core.coverage import CoverageMap, validate_coverage_settings
from biofuzz.docking.config import PocketConfig
from biofuzz.docking.parser import PoseAtom
from biofuzz.protein.pocket import compute_fingerprint
from biofuzz.protein.residues import parse_protein_residues


def test_coverage_update_and_persistence(tmp_path: Path) -> None:
    protein_residues = {
        "A:8": [(0.0, 0.0, 0.0)],
        "A:23": [(5.0, 5.0, 5.0)],
        "A:25": [(9.0, 9.0, 9.0)],
    }
    pocket = PocketConfig(residue_ids={"A:8", "A:23", "A:25"}, contact_cutoff=3.5)

    atoms_a = [PoseAtom("C1", 0.5, 0.2, 0.1, 0.0, "C")]
    atoms_b = [PoseAtom("C1", 5.2, 5.1, 5.0, 0.0, "C")]

    fp1 = compute_fingerprint(atoms_a, protein_residues, pocket)
    fp2 = compute_fingerprint(atoms_b, protein_residues, pocket)

    cov = CoverageMap(pocket.residue_ids)
    obs1 = cov.observe(fp1)
    obs2 = cov.observe(fp2)
    obs3 = cov.observe(fp1)

    assert obs1.novelty_class == "strong"
    assert obs2.novelty_class in {"strong", "weak", "none"}
    assert obs3.novelty_class == "none"

    save_path = tmp_path / "coverage.json"
    cov.save(save_path)

    loaded = CoverageMap(set())
    loaded.load(save_path)
    assert loaded.pocket_residue_ids == cov.pocket_residue_ids
    assert loaded.current_map == cov.current_map
    assert loaded.previous_map == cov.previous_map


def test_coverage_mapping_and_hash_are_stable_for_same_pocket() -> None:
    cov_a = CoverageMap({"A:25", "A:8", "A:23"}, map_size_bytes=1024)
    cov_b = CoverageMap({"A:8", "A:23", "A:25"}, map_size_bytes=1024)

    assert cov_a.residue_to_bit_index == {"A:8": 0, "A:23": 1, "A:25": 2}
    assert cov_a.residue_to_bit_index == cov_b.residue_to_bit_index

    fingerprint = frozenset({"A:8", "A:25"})
    valid_a, mask_a = cov_a.fingerprint_to_mask(fingerprint)
    valid_b, mask_b = cov_b.fingerprint_to_mask(fingerprint)

    assert valid_a == fingerprint
    assert valid_b == fingerprint
    assert mask_a == mask_b
    assert cov_a.hash_mask(mask_a) == cov_b.hash_mask(mask_b)


def test_coverage_observe_classifies_strong_weak_and_none() -> None:
    cov = CoverageMap({"A:8", "A:23", "A:25"}, map_size_bytes=1024)

    strong = cov.observe(frozenset({"A:8", "A:25"}))
    cov._rotate_epoch()
    weak = cov.observe(frozenset({"A:8", "A:25"}))
    none = cov.observe(frozenset({"A:8", "A:25"}))

    assert strong.novelty_class == "strong"
    assert strong.novelty_score == 2
    assert weak.novelty_class == "weak"
    assert weak.novelty_score == 1
    assert none.novelty_class == "none"
    assert none.novelty_score == 0
    assert cov.novelty_counts() == {"strong": 1, "weak": 1, "none": 1}


def test_coverage_rotates_epoch_when_occupancy_threshold_is_crossed() -> None:
    cov = CoverageMap({"A:8"}, map_size_bytes=1024, occupancy_rotate_threshold=0.1)
    valid_fingerprint, mask = cov.fingerprint_to_mask(frozenset({"A:8"}))
    hash_index = cov.hash_mask(mask)
    threshold_count = int(cov.map_size_bytes * cov.occupancy_rotate_threshold) + 1
    for offset in range(threshold_count - 1):
        cov.current_map[(hash_index + offset + 1) % cov.map_size_bytes] = 1
    cov.current_nonzero_count = threshold_count - 1

    observation = cov.observe(valid_fingerprint)

    assert observation.rotated is True
    assert observation.epoch == 1
    assert cov.epoch == 1
    assert cov.bitmap_occupancy() == 0.0
    assert cov.previous_map[hash_index] == 1


def test_coverage_loads_legacy_union_only_checkpoint(tmp_path: Path) -> None:
    save_path = tmp_path / "coverage_legacy.json"
    save_path.write_text(
        json.dumps(
            {
                "pocket_residue_ids": [8, 23, 25],
                "global_coverage": [8, 25],
            }
        ),
        encoding="utf-8",
    )

    cov = CoverageMap({"A:8", "A:23", "A:25"})
    cov.load(save_path)

    assert cov.pocket_residue_ids == {"A:8", "A:23", "A:25"}
    assert cov.epoch == 0
    assert cov.novelty_counts() == {"strong": 0, "weak": 0, "none": 0}


def test_coverage_rejects_ambiguous_legacy_checkpoint_on_multimeric_pocket(
    tmp_path: Path,
) -> None:
    save_path = tmp_path / "coverage_legacy_multimer.json"
    save_path.write_text(
        json.dumps(
            {
                "pocket_residue_ids": [42],
                "global_coverage": [42],
            }
        ),
        encoding="utf-8",
    )

    cov = CoverageMap({"A:42", "B:42"})

    with pytest.raises(ValueError, match="multiple chain-aware target residues"):
        cov.load(save_path)


def test_validate_coverage_settings_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="power of two"):
        validate_coverage_settings(map_size_bytes=250 * 1024, occupancy_rotate_threshold=0.55)

    with pytest.raises(ValueError, match="between 0.1 and 0.95"):
        validate_coverage_settings(map_size_bytes=1024, occupancy_rotate_threshold=0.01)


def test_coverage_ignores_residues_outside_defined_pocket() -> None:
    cov = CoverageMap({"A:8", "A:23", "A:25"})
    observation = cov.observe(frozenset({"A:8", "A:99"}))

    assert observation.fingerprint == frozenset({"A:8"})
    assert observation.novelty_class == "strong"


def test_compute_fingerprint_ignores_hydrogen_only_contacts() -> None:
    protein_residues = {"A:8": [(0.0, 0.0, 0.0)]}
    pocket = PocketConfig(residue_ids={"A:8"}, contact_cutoff=3.5)
    atoms = [
        PoseAtom("H1", 0.1, 0.1, 0.1, 0.0, "HD"),
        PoseAtom("C1", 10.0, 10.0, 10.0, 0.0, "C"),
    ]

    assert compute_fingerprint(atoms, protein_residues, pocket) == frozenset()


def test_compute_fingerprint_distinguishes_same_residue_number_across_chains() -> None:
    protein_residues = {
        "A:42": [(0.0, 0.0, 0.0)],
        "B:42": [(10.0, 10.0, 10.0)],
    }
    pocket = PocketConfig(residue_ids={"A:42", "B:42"}, contact_cutoff=3.5)

    atoms_a = [PoseAtom("C1", 0.1, 0.1, 0.1, 0.0, "C")]
    atoms_b = [PoseAtom("C1", 10.1, 10.1, 10.1, 0.0, "C")]

    fingerprint_a = compute_fingerprint(atoms_a, protein_residues, pocket)
    fingerprint_b = compute_fingerprint(atoms_b, protein_residues, pocket)
    cov = CoverageMap(pocket.residue_ids)

    assert fingerprint_a == frozenset({"A:42"})
    assert fingerprint_b == frozenset({"B:42"})
    assert cov.fingerprint_to_mask(fingerprint_a)[1] != cov.fingerprint_to_mask(fingerprint_b)[1]


def test_parse_protein_residues_skips_receptor_hydrogens() -> None:
    pdbqt = (
        "ATOM      1  H1  MET A   8       0.000   0.000   0.000  0.00  0.00  0.000 HD\n"
        "ATOM      2  CA  MET A   8       1.000   1.000   1.000  0.00  0.00  0.000 C\n"
    )

    assert parse_protein_residues(pdbqt) == {"A:8": [(1.0, 1.0, 1.0)]}
