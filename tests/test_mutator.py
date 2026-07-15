from rdkit import Chem

from biofuzz.mutator import mutate_with_metadata

PHENOL = "c1ccc(O)cc1"


def test_deterministic_stage_produces_valid_distinct_mutants():
    candidates = mutate_with_metadata(PHENOL, n=50, stage="deterministic", seed=1)
    assert len(candidates) >= 10
    assert all(c.stage == "deterministic" for c in candidates)
    assert all(Chem.MolFromSmiles(c.smiles) is not None for c in candidates)
    assert all(c.parent_smiles == PHENOL for c in candidates)
    # distinct
    assert len({c.smiles for c in candidates}) == len(candidates)


def test_splice_stage_with_donor():
    splice_candidates = mutate_with_metadata(
        PHENOL, n=10, stage="splice", donor_smiles="c1ccncc1C(=O)N", seed=2
    )
    assert len(splice_candidates) >= 1
    assert all(c.stage == "splice" for c in splice_candidates)
    assert all(Chem.MolFromSmiles(c.smiles) is not None for c in splice_candidates)


def test_splice_stage_without_donor_returns_empty():
    assert mutate_with_metadata(PHENOL, n=10, stage="splice", seed=3) == []


def test_havoc_stage_produces_novel_valid_molecules():
    havoc_candidates = mutate_with_metadata(PHENOL, n=20, stage="havoc", seed=4)
    assert len(havoc_candidates) >= 5
    assert PHENOL not in {c.smiles for c in havoc_candidates}  # no identity
    assert all(c.stage == "havoc" for c in havoc_candidates)
    assert all(Chem.MolFromSmiles(c.smiles) is not None for c in havoc_candidates)


def test_invalid_parent_smiles_returns_empty():
    assert mutate_with_metadata("not smiles", n=10, stage="deterministic") == []


def test_unknown_stage_raises():
    import pytest

    with pytest.raises(ValueError):
        mutate_with_metadata(PHENOL, n=5, stage="bogus")


def test_all_candidates_respect_tight_filter_bounds():
    candidates = mutate_with_metadata(
        PHENOL, n=30, stage="deterministic", max_mw=200.0, seed=5
    )
    from rdkit.Chem import Descriptors

    for c in candidates:
        mol = Chem.MolFromSmiles(c.smiles)
        assert Descriptors.MolWt(mol) <= 200.0


def test_mutation_type_is_populated():
    candidates = mutate_with_metadata(PHENOL, n=10, stage="deterministic", seed=6)
    assert all(c.mutation_type for c in candidates)
    havoc_candidates = mutate_with_metadata(PHENOL, n=10, stage="havoc", seed=7)
    assert all(c.mutation_type for c in havoc_candidates)
