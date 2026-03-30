from pathlib import Path

from biofuzz.core.coverage import CoverageMap
from biofuzz.docking.config import PocketConfig
from biofuzz.docking.parser import PoseAtom
from biofuzz.protein.pocket import compute_fingerprint


def test_coverage_update_and_persistence(tmp_path: Path) -> None:
    protein_residues = {
        8: [(0.0, 0.0, 0.0)],
        23: [(5.0, 5.0, 5.0)],
        25: [(9.0, 9.0, 9.0)],
    }
    pocket = PocketConfig(residue_ids={8, 23, 25}, contact_cutoff=3.5)

    atoms_a = [PoseAtom("C1", 0.5, 0.2, 0.1, 0.0, "C")]
    atoms_b = [PoseAtom("C1", 5.2, 5.1, 5.0, 0.0, "C")]

    fp1 = compute_fingerprint(atoms_a, protein_residues, pocket)
    fp2 = compute_fingerprint(atoms_b, protein_residues, pocket)

    cov = CoverageMap(pocket.residue_ids)
    new1 = cov.update(fp1)
    new2 = cov.update(fp2)
    new3 = cov.update(fp1)

    assert len(new1) > 0
    assert len(new2) > 0
    assert new3 == frozenset()

    save_path = tmp_path / "coverage.json"
    cov.save(save_path)

    loaded = CoverageMap(set())
    loaded.load(save_path)
    assert loaded.global_coverage == cov.global_coverage
    assert loaded.pocket_residue_ids == cov.pocket_residue_ids


def test_coverage_ignores_residues_outside_defined_pocket() -> None:
    cov = CoverageMap({8, 23, 25})
    new_bits = cov.update(frozenset({8, 99}))

    assert new_bits == frozenset({8})
    assert cov.global_coverage == {8}
    assert cov.coverage_ratio() == 1.0 / 3.0


def test_compute_fingerprint_ignores_hydrogen_only_contacts() -> None:
    protein_residues = {8: [(0.0, 0.0, 0.0)]}
    pocket = PocketConfig(residue_ids={8}, contact_cutoff=3.5)
    atoms = [
        PoseAtom("H1", 0.1, 0.1, 0.1, 0.0, "HD"),
        PoseAtom("C1", 10.0, 10.0, 10.0, 0.0, "C"),
    ]

    assert compute_fingerprint(atoms, protein_residues, pocket) == frozenset()
