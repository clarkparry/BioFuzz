import pytest

from biofuzz.molecules import mutator


@pytest.mark.skipif(mutator.Chem is None, reason="RDKit not installed")
def test_mutate_returns_distinct_valid_smiles() -> None:
    mutants = mutator.mutate("c1ccc(O)cc1", n=20, seed=7)
    assert len(mutants) >= 5
    assert "c1ccc(O)cc1" not in mutants

    for smi in mutants:
        assert mutator.Chem.MolFromSmiles(smi) is not None


def test_mutate_handles_zero_n() -> None:
    assert mutator.mutate("c1ccccc1", n=0) == []
