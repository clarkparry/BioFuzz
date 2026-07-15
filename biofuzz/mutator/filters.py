from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import Descriptors


def passes_drug_likeness(
    mol: Chem.Mol,
    min_mw: float,
    max_mw: float,
    max_logp: float,
    max_hbd: int,
    max_hba: int,
    max_rot_bonds: int,
) -> bool:
    if mol is None:
        return False
    if len(Chem.GetMolFrags(mol)) != 1:
        return False
    mw = Descriptors.MolWt(mol)
    if mw < min_mw or mw > max_mw:
        return False
    if Descriptors.MolLogP(mol) > max_logp:
        return False
    if Descriptors.NumHDonors(mol) > max_hbd:
        return False
    if Descriptors.NumHAcceptors(mol) > max_hba:
        return False
    if Descriptors.NumRotatableBonds(mol) > max_rot_bonds:
        return False
    return True
