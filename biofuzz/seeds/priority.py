from __future__ import annotations

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors
from rdkit.Chem.Scaffolds import MurckoScaffold

RDLogger.DisableLog("rdApp.*")

TARGET_SPECIFIC_BONUS = 5.0
MW_REFERENCE = 350.0  # midpoint of the seeds.md drug-likeness MW band
MW_BONUS_SCALE = 2.0
LOGP_BONUS_SCALE = 1.0
SCAFFOLD_DIVERSITY_BONUS = 3.0


def base_affinity_estimate(smiles: str) -> float:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0.0
    mw = Descriptors.MolWt(mol)
    logp = Descriptors.MolLogP(mol)

    mw_bonus = max(0.0, (mw - MW_REFERENCE) / 100.0) * MW_BONUS_SCALE
    logp_bonus = max(0.0, (3.0 - logp) / 3.0) * LOGP_BONUS_SCALE
    return mw_bonus + logp_bonus


def scaffold_diversity_bonus(smiles: str, corpus_scaffold_counts: dict[str, int] | None) -> float:
    if not corpus_scaffold_counts:
        return SCAFFOLD_DIVERSITY_BONUS
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0.0
    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
    count = corpus_scaffold_counts.get(scaffold, 0)
    if count == 0:
        return SCAFFOLD_DIVERSITY_BONUS
    return max(0.0, SCAFFOLD_DIVERSITY_BONUS - count)


def compute_seed_priority(
    smiles: str,
    target_specific: bool = False,
    corpus_scaffold_counts: dict[str, int] | None = None,
) -> float:
    priority = base_affinity_estimate(smiles) + scaffold_diversity_bonus(
        smiles, corpus_scaffold_counts
    )
    if target_specific:
        priority += TARGET_SPECIFIC_BONUS
    return max(0.1, priority)
