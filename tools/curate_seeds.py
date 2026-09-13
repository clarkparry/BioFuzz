#!/usr/bin/env python3
"""Curate a raw candidate SMILES list into seeds/approved_drugs.smi.

Input: a text file with one SMILES per line (optionally followed by an ID),
sourced externally (e.g. a ChEMBL/DrugBank/PubChem approved-drug export).
This script canonicalizes, applies the drug-likeness bounds from
docs/architecture.md, deduplicates, and caps scaffold repeats for
diversity, per seeds.md's "Seed Set Maintenance" section.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors
from rdkit.Chem.Scaffolds import MurckoScaffold

RDLogger.DisableLog("rdApp.*")

MIN_MW = 150.0
MAX_MW = 500.0
MIN_LOGP = -1.0
MAX_LOGP = 5.0
MAX_SCAFFOLD_REPEATS = 2
MAX_AMIDE_BONDS = 3  # crude peptide screen
AMIDE_PATTERN = Chem.MolFromSmarts("C(=O)N")


def _passes_filters(mol: Chem.Mol) -> bool:
    if mol is None:
        return False
    if len(Chem.GetMolFrags(mol)) != 1:
        return False
    mw = Descriptors.MolWt(mol)
    if mw < MIN_MW or mw > MAX_MW:
        return False
    logp = Descriptors.MolLogP(mol)
    if logp < MIN_LOGP or logp > MAX_LOGP:
        return False
    if len(mol.GetSubstructMatches(AMIDE_PATTERN)) > MAX_AMIDE_BONDS:
        return False
    return True


def _parse_input_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.lower() == "smiles" or line.startswith("#"):
        return None
    parts = line.split(None, 1)
    smiles = parts[0]
    seed_id = parts[1].strip() if len(parts) > 1 else None
    return smiles, seed_id


def curate(input_path: Path, output_path: Path, max_total: int = 250) -> int:
    seen_canonical: set[str] = set()
    scaffold_counts: dict[str, int] = {}
    curated: list[tuple[str, str]] = []

    counter = 0
    for raw_line in input_path.read_text().splitlines():
        parsed = _parse_input_line(raw_line)
        if parsed is None:
            continue
        smiles, given_id = parsed

        mol = Chem.MolFromSmiles(smiles)
        if not _passes_filters(mol):
            continue

        canonical = Chem.MolToSmiles(mol, canonical=True)
        if canonical in seen_canonical:
            continue

        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
        if scaffold_counts.get(scaffold, 0) >= MAX_SCAFFOLD_REPEATS:
            continue

        seen_canonical.add(canonical)
        scaffold_counts[scaffold] = scaffold_counts.get(scaffold, 0) + 1
        counter += 1
        seed_id = given_id or f"approved_{counter:03d}"
        curated.append((canonical, seed_id))

        if len(curated) >= max_total:
            break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        for smiles, seed_id in curated:
            fh.write(f"{smiles} {seed_id}\n")

    return len(curated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Raw candidate SMILES file")
    parser.add_argument("--output", default="seeds/approved_drugs.smi")
    parser.add_argument("--max-total", type=int, default=250)
    args = parser.parse_args()

    n = curate(Path(args.input), Path(args.output), args.max_total)
    print(f"Wrote {n} curated seeds to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
