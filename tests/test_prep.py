from biofuzz.prep import prepare_smiles

PERMISSIVE = dict(
    min_mw=0.0, max_mw=550.0, max_logp=5.0, max_hbd=10, max_hba=10, max_rot_bonds=15
)


def test_valid_drug_like_molecule_produces_pdbqt():
    pdbqt = prepare_smiles("CC(=O)Oc1ccccc1C(=O)O", **PERMISSIVE)  # aspirin
    assert pdbqt is not None
    assert "TORSDOF" in pdbqt
    torsdof_value = pdbqt.split("TORSDOF")[1].strip().splitlines()[0].strip()
    assert ("BRANCH" in pdbqt and "ENDBRANCH" in pdbqt) or torsdof_value == "0"


def test_invalid_smiles_returns_none():
    assert prepare_smiles("this is not smiles", **PERMISSIVE) is None


def test_doc_peptide_example_is_invalid_smiles():
    assert prepare_smiles("ACDEFGHIKLMNPQRSTVWY", **PERMISSIVE) is None


def test_molecule_failing_mw_filter_returns_none():
    c40_alkane = "C" * 40
    assert prepare_smiles(c40_alkane, **{**PERMISSIVE, "max_mw": 550.0}) is None


def test_repeated_call_produces_stable_torsdof():
    kwargs = dict(PERMISSIVE)
    pdbqt1 = prepare_smiles("CC(=O)Oc1ccccc1C(=O)O", **kwargs)
    pdbqt2 = prepare_smiles("CC(=O)Oc1ccccc1C(=O)O", **kwargs)
    assert pdbqt1 is not None and pdbqt2 is not None
    assert pdbqt1.split("TORSDOF")[1] == pdbqt2.split("TORSDOF")[1]


def test_disconnected_fragments_rejected():
    assert prepare_smiles("[Na+].[Cl-]", **PERMISSIVE) is None


def test_uff_fallback_still_succeeds():
    pdbqt = prepare_smiles("OB(O)c1ccccc1", **PERMISSIVE)  # phenylboronic acid
    assert pdbqt is not None
    assert "TORSDOF" in pdbqt


def test_tight_filter_bound_rejects_otherwise_fine_molecule():
    kwargs = {**PERMISSIVE, "max_rot_bonds": 0}
    assert prepare_smiles("CC(=O)Oc1ccccc1C(=O)O", **kwargs) is None
