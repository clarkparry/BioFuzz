from __future__ import annotations

import random

from rdkit import Chem, RDLogger

from biofuzz.mutator.deterministic import atom_scan, halogen_scan, substituent_scan
from biofuzz.mutator.entry import MutationCandidate
from biofuzz.mutator.filters import passes_drug_likeness
from biofuzz.mutator.havoc import havoc_mutate
from biofuzz.mutator.splice import fragment_graft, scaffold_splice

RDLogger.DisableLog("rdApp.*")


def mutate_with_metadata(
    smiles: str,
    n: int,
    stage: str,
    donor_smiles: str | None = None,
    min_mw: float = 0.0,
    max_mw: float = 550.0,
    max_logp: float = 5.0,
    max_hbd: int = 10,
    max_hba: int = 10,
    max_rot_bonds: int = 10,
    seed: int | None = None,
) -> list[MutationCandidate]:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return []

    rng = random.Random(seed)
    filter_kwargs = dict(
        min_mw=min_mw,
        max_mw=max_mw,
        max_logp=max_logp,
        max_hbd=max_hbd,
        max_hba=max_hba,
        max_rot_bonds=max_rot_bonds,
    )

    def _valid(candidate_smiles: str) -> bool:
        cand_mol = Chem.MolFromSmiles(candidate_smiles)
        return passes_drug_likeness(cand_mol, **filter_kwargs)

    seen: set[str] = {smiles}
    results: list[MutationCandidate] = []

    if stage == "deterministic":
        raw = atom_scan(mol, smiles) + substituent_scan(mol, smiles) + halogen_scan(mol, smiles)
        rng.shuffle(raw)
        for cand_smiles, mutation_type in raw:
            if len(results) >= n:
                break
            if cand_smiles in seen or not _valid(cand_smiles):
                continue
            seen.add(cand_smiles)
            results.append(
                MutationCandidate(
                    smiles=cand_smiles,
                    stage="deterministic",
                    mutation_type=mutation_type,
                    parent_smiles=smiles,
                )
            )

    elif stage == "splice":
        if donor_smiles is None:
            return []
        donor_mol = Chem.MolFromSmiles(donor_smiles)
        if donor_mol is None:
            return []

        splice_ops = [("scaffold_splice", scaffold_splice), ("fragment_graft", fragment_graft)]
        max_attempts = max(50, n * 20)
        for _ in range(max_attempts):
            if len(results) >= n:
                break
            op_name, op_fn = rng.choice(splice_ops)
            result = op_fn(mol, donor_mol, rng)
            if result is None:
                continue
            cand_smiles = Chem.MolToSmiles(result)
            if cand_smiles in seen or not _valid(cand_smiles):
                continue
            seen.add(cand_smiles)
            results.append(
                MutationCandidate(
                    smiles=cand_smiles, stage="splice", mutation_type=op_name, parent_smiles=smiles
                )
            )

    elif stage == "havoc":
        max_attempts = max(50, n * 20)
        for _ in range(max_attempts):
            if len(results) >= n:
                break
            result, applied_ops = havoc_mutate(mol, rng)
            if result is None:
                continue
            cand_smiles = Chem.MolToSmiles(result)
            if cand_smiles in seen or not _valid(cand_smiles):
                continue
            seen.add(cand_smiles)
            results.append(
                MutationCandidate(
                    smiles=cand_smiles,
                    stage="havoc",
                    mutation_type="+".join(applied_ops),
                    parent_smiles=smiles,
                )
            )

    else:
        raise ValueError(f"unknown mutation stage: {stage}")

    return results
