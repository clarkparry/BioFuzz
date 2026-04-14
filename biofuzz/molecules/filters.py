from __future__ import annotations

from dataclasses import dataclass

try:
    from rdkit import Chem
    from rdkit.Chem import Crippen, Descriptors, Lipinski
except ImportError:  # pragma: no cover - handled in runtime checks
    Chem = None  # type: ignore[assignment]
    Crippen = Descriptors = Lipinski = None  # type: ignore[assignment]


@dataclass(frozen=True)
class DrugLikeMetrics:
    molecular_weight: float
    logp: float
    hbd: int
    hba: int
    rot_bonds: int


def rdkit_available() -> bool:
    return Chem is not None


def drug_like_metrics_mol(mol) -> DrugLikeMetrics | None:
    if Chem is None or mol is None:
        return None

    if len(Chem.GetMolFrags(mol)) != 1:
        return None

    return DrugLikeMetrics(
        molecular_weight=float(Descriptors.MolWt(mol)),
        logp=float(Crippen.MolLogP(mol)),
        hbd=int(Lipinski.NumHDonors(mol)),
        hba=int(Lipinski.NumHAcceptors(mol)),
        rot_bonds=int(Lipinski.NumRotatableBonds(mol)),
    )


def drug_like_metrics(smiles: str) -> DrugLikeMetrics | None:
    if Chem is None:
        return None

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    return drug_like_metrics_mol(mol)


def is_drug_like_mol(
    mol,
    min_mw: float = 0.0,
    max_mw: float = 550.0,
    max_logp: float = 5.0,
    max_hbd: int = 5,
    max_hba: int = 10,
    max_rot_bonds: int = 10,
) -> bool:
    metrics = drug_like_metrics_mol(mol)
    if metrics is None:
        return False

    return all(
        (
            min_mw <= metrics.molecular_weight <= max_mw,
            metrics.logp <= max_logp,
            metrics.hbd <= max_hbd,
            metrics.hba <= max_hba,
            metrics.rot_bonds <= max_rot_bonds,
        )
    )


def is_drug_like(
    smiles: str,
    min_mw: float = 0.0,
    max_mw: float = 550.0,
    max_logp: float = 5.0,
    max_hbd: int = 5,
    max_hba: int = 10,
    max_rot_bonds: int = 10,
) -> bool:
    if Chem is None:
        return False

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return False

    return is_drug_like_mol(
        mol,
        min_mw=min_mw,
        max_mw=max_mw,
        max_logp=max_logp,
        max_hbd=max_hbd,
        max_hba=max_hba,
        max_rot_bonds=max_rot_bonds,
    )
