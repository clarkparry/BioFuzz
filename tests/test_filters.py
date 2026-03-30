import pytest

from biofuzz.molecules import filters


@pytest.mark.skipif(filters.Chem is None, reason="RDKit not installed")
def test_is_drug_like_accepts_aspirin() -> None:
    assert filters.is_drug_like("CC(=O)Oc1ccccc1C(=O)O")


@pytest.mark.skipif(filters.Chem is None, reason="RDKit not installed")
def test_is_drug_like_rejects_disconnected_smiles() -> None:
    assert not filters.is_drug_like("CCO.CN")


@pytest.mark.skipif(filters.Chem is None, reason="RDKit not installed")
def test_is_drug_like_respects_threshold_boundaries() -> None:
    metrics = filters.drug_like_metrics("CCO")
    assert metrics is not None

    assert filters.is_drug_like(
        "CCO",
        min_mw=metrics.molecular_weight,
        max_mw=metrics.molecular_weight,
        max_logp=metrics.logp,
        max_hbd=metrics.hbd,
        max_hba=metrics.hba,
        max_rot_bonds=metrics.rot_bonds,
    )

    assert not filters.is_drug_like(
        "CCO",
        min_mw=metrics.molecular_weight + 0.1,
    )
