from __future__ import annotations

from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem, Descriptors

RDLogger.DisableLog("rdApp.*")

try:
    from meeko import MoleculePreparation, PDBQTWriterLegacy
except ImportError:  # pragma: no cover
    MoleculePreparation = None
    PDBQTWriterLegacy = None


def _passes_drug_likeness(
    mol: Chem.Mol,
    min_mw: float,
    max_mw: float,
    max_logp: float,
    max_hbd: int,
    max_hba: int,
    max_rot_bonds: int,
) -> bool:
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


def _embed(molH: Chem.Mol, retries: int) -> int | None:
    for seed in range(retries):
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        conf_id = AllChem.EmbedMolecule(molH, params)
        if conf_id != -1:
            return conf_id
    return None


def _optimize(molH: Chem.Mol, conf_id: int) -> None:
    try:
        result = AllChem.MMFFOptimizeMolecule(molH, confId=conf_id)
        if result == -1:
            raise ValueError("MMFF params unavailable")
    except Exception:
        try:
            AllChem.UFFOptimizeMolecule(molH, confId=conf_id)
        except Exception:
            pass


def _to_pdbqt(molH: Chem.Mol) -> str | None:
    if MoleculePreparation is None:
        return None
    try:
        preparator = MoleculePreparation()
        mol_setups = preparator.prepare(molH)
        if not mol_setups:
            return None
        pdbqt_string, is_ok, _err_msg = PDBQTWriterLegacy.write_string(mol_setups[0])
        if not is_ok or not pdbqt_string:
            return None
        return pdbqt_string
    except Exception:
        return None


def _is_valid_pdbqt(pdbqt: str) -> bool:
    if "TORSDOF" not in pdbqt:
        return False
    torsdof_value = pdbqt.split("TORSDOF")[1].strip().splitlines()[0].strip()
    if torsdof_value == "0":
        return True
    return "BRANCH" in pdbqt and "ENDBRANCH" in pdbqt


def prepare_smiles(
    smiles: str,
    min_mw: float,
    max_mw: float,
    max_logp: float,
    max_hbd: int,
    max_hba: int,
    max_rot_bonds: int,
    retries: int = 3,
) -> str | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    if not _passes_drug_likeness(
        mol, min_mw, max_mw, max_logp, max_hbd, max_hba, max_rot_bonds
    ):
        return None

    molH = Chem.AddHs(mol)
    conf_id = _embed(molH, retries)
    if conf_id is None:
        return None

    _optimize(molH, conf_id)

    pdbqt = _to_pdbqt(molH)
    if pdbqt is None:
        return None

    if not _is_valid_pdbqt(pdbqt):
        return None

    return pdbqt
