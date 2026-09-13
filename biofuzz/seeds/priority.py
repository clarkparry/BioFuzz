from __future__ import annotations

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors
from rdkit.Chem.Scaffolds import MurckoScaffold

RDLogger.DisableLog("rdApp.*")

MW_REFERENCE = 350.0  # midpoint of the seeds.md drug-likeness MW band
MW_BONUS_SCALE = 2.0
LOGP_BONUS_SCALE = 1.0
SCAFFOLD_DIVERSITY_BONUS = 3.0
# Half-width of the MW band, in daltons. A seed at MW_REFERENCE scores full
# marks; one MW_TOLERANCE away scores zero.
MW_TOLERANCE = 200.0


def base_affinity_estimate(smiles: str) -> float:
    """Prior on a seed's worth, before anything has been docked.

    The MW term is deliberately a band centred on MW_REFERENCE, and must not
    become a reward for being heavy. An unbounded ramp in MW would compound
    docking's own size bias -- raw affinity already scales with heavy-atom
    count -- so the campaign would start from the largest seed available and
    climb the molecular-weight gradient from there. Ranking on closeness to the
    middle of the drug-like band lets small, ligand-efficient seeds compete.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0.0
    mw = Descriptors.MolWt(mol)
    logp = Descriptors.MolLogP(mol)

    mw_bonus = max(0.0, 1.0 - abs(mw - MW_REFERENCE) / MW_TOLERANCE) * MW_BONUS_SCALE
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
    corpus_scaffold_counts: dict[str, int] | None = None,
) -> float:
    priority = base_affinity_estimate(smiles) + scaffold_diversity_bonus(
        smiles, corpus_scaffold_counts
    )
    return max(0.1, priority)
