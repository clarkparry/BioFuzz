from pathlib import Path

import pytest
from rdkit import Chem

from biofuzz.mutator.alerts import (
    is_chemically_plausible,
    unstable_motifs,
    unstable_motifs_for_smiles,
)
from biofuzz.mutator.filters import passes_drug_likeness

SEEDS = Path(__file__).resolve().parents[1] / "seeds" / "approved_drugs.smi"

PERMISSIVE = dict(min_mw=0.0, max_mw=2000.0, max_logp=99.0, max_hbd=99, max_hba=99, max_rot_bonds=99)

# The two motifs the reference campaign actually generated and saved as findings:
# an aryl-O-N-O and an aryl-O-CH2-O-H, both produced by atom_scan walking
# sildenafil's ethoxy chain one atom at a time.
CAMPAIGN_GARBAGE = {
    "n_o_single_bond": "CCCc1nn(N)c2c(=O)[nH]c(-c3c(N)c(S(=O)(=O)C4CCC(N)CC4)cc(F)c3ONO)cc12",
    "acyclic_acetal": "CCCc1nn(C)c2c(=O)[nH]c(-c3c(N)c(S(=O)(=O)C4CCC(N)CC4)cc(F)c3OCO)cc12",
}

# Real drugs whose motifs a naive version of these patterns would wrongly reject.
TRICKY_BUT_REAL = {
    "artemisinin": "CC1CCC2C(C)C(=O)OC3OC4(C)CCC1C32OO4",  # ring endoperoxide
    "paroxetine": "Fc1ccc([C@@H]2CCNC[C@H]2COc2ccc3c(c2)OCO3)cc1",  # benzodioxole
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",  # phenol ester
    "vorinostat": "O=C(NO)CCCCCCC(=O)Nc1ccccc1",  # hydroxamic acid
    "sildenafil": "CCCc1nn(C)c2c(=O)[nH]c(-c3cc(S(=O)(=O)N4CCN(C)CC4)ccc3OCC)nc12",
}


@pytest.mark.parametrize("expected_motif,smiles", CAMPAIGN_GARBAGE.items())
def test_campaign_garbage_is_flagged(expected_motif, smiles):
    motifs = unstable_motifs_for_smiles(smiles)
    assert expected_motif in motifs
    assert not is_chemically_plausible(Chem.MolFromSmiles(smiles))


@pytest.mark.parametrize("name,smiles", TRICKY_BUT_REAL.items())
def test_real_drugs_with_tricky_motifs_pass(name, smiles):
    assert unstable_motifs_for_smiles(smiles) == [], name
    assert is_chemically_plausible(Chem.MolFromSmiles(smiles)), name


def test_no_false_positives_across_approved_seed_corpus():
    """The filter must not reject the seed corpus it is meant to mutate."""
    flagged = []
    for line in SEEDS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        smiles = line.split()[0]
        motifs = unstable_motifs_for_smiles(smiles)
        if motifs:
            flagged.append((smiles, motifs))
    assert flagged == [], f"approved drugs wrongly flagged: {flagged}"


def test_specific_unstable_motifs():
    assert "acyclic_peroxide" in unstable_motifs_for_smiles("CCOOCC")
    assert "gem_diol" in unstable_motifs_for_smiles("CC(O)(O)C")
    assert "acyl_halide" in unstable_motifs_for_smiles("CC(=O)Cl")
    assert "n_halide" in unstable_motifs_for_smiles("CN(C)Cl")
    assert "acyclic_aminal" in unstable_motifs_for_smiles("CN(C)CN(C)C")


def test_invalid_molecule():
    assert unstable_motifs(None) == ["invalid_molecule"]
    assert not is_chemically_plausible(None)


def test_drug_likeness_gate_rejects_unstable_and_can_be_disabled():
    garbage = Chem.MolFromSmiles(CAMPAIGN_GARBAGE["n_o_single_bond"])

    # Property bounds alone accept it -- that is why it became a finding.
    assert passes_drug_likeness(garbage, check_stability=False, **PERMISSIVE)
    assert not passes_drug_likeness(garbage, check_stability=True, **PERMISSIVE)


def test_drug_likeness_gate_still_accepts_real_drugs():
    for name, smiles in TRICKY_BUT_REAL.items():
        mol = Chem.MolFromSmiles(smiles)
        assert passes_drug_likeness(mol, **PERMISSIVE), name
