import pytest

from biofuzz.molecules import preparation


@pytest.mark.skipif(
    preparation.Chem is None or not preparation.meeko_available(),
    reason="RDKit/Meeko not installed",
)
def test_prepare_smiles_valid_returns_pdbqt() -> None:
    pdbqt = preparation.prepare_smiles("CC(=O)Oc1ccccc1C(=O)O")
    assert pdbqt is not None
    assert "TORSDOF" in pdbqt


@pytest.mark.skipif(preparation.Chem is None, reason="RDKit not installed")
def test_prepare_smiles_invalid_returns_none() -> None:
    assert preparation.prepare_smiles("not_a_smiles") is None


@pytest.mark.skipif(preparation.Chem is None, reason="RDKit not installed")
def test_prepare_smiles_embedding_failure_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(preparation, "_embed_with_retries", lambda mol, retries=3: False)
    assert preparation.prepare_smiles("CC(=O)Oc1ccccc1C(=O)O") is None


@pytest.mark.skipif(preparation.Chem is None, reason="RDKit not installed")
def test_prepare_smiles_returns_none_when_meeko_output_missing(monkeypatch) -> None:
    monkeypatch.setattr(preparation, "_pdbqt_with_meeko", lambda mol: None)

    assert preparation.prepare_smiles("CCO") is None


@pytest.mark.skipif(preparation.Chem is None, reason="RDKit not installed")
def test_prepare_smiles_can_opt_into_fallback_writer(monkeypatch) -> None:
    monkeypatch.setattr(preparation, "_pdbqt_with_meeko", lambda mol: None)
    monkeypatch.setattr(preparation, "_pdbqt_fallback", lambda mol: "TORSDOF 0\n")

    assert preparation.prepare_smiles("CCO", allow_fallback=True) == "TORSDOF 0\n"


@pytest.mark.skipif(preparation.Chem is None, reason="RDKit not installed")
def test_prepare_smiles_forwards_drug_like_thresholds(monkeypatch) -> None:
    captured: dict[str, float | int] = {}

    def fake_is_drug_like(smiles: str, **kwargs):
        captured.update(kwargs)
        return False

    monkeypatch.setattr(preparation, "is_drug_like", fake_is_drug_like)

    assert (
        preparation.prepare_smiles(
            "CCO",
            max_mw=200.0,
            max_logp=3.2,
            max_hbd=4,
            max_hba=8,
            max_rot_bonds=6,
        )
        is None
    )
    assert captured["max_mw"] == 200.0
    assert captured["max_logp"] == 3.2
    assert captured["max_hbd"] == 4
    assert captured["max_hba"] == 8
    assert captured["max_rot_bonds"] == 6
