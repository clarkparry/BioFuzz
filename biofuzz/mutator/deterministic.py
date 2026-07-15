from __future__ import annotations

from rdkit import Chem

from biofuzz.mutator.ops import HALOGENS, SUBSTITUENT_FRAGMENTS, attach_fragment, open_aromatic_positions, swap_atom_type

ATOM_SCAN_TARGETS = {"C": ["N", "O"], "N": ["C"], "S": ["O"]}


def atom_scan(mol, parent_smiles: str) -> list[tuple[str, str]]:
    candidates = []
    for atom in mol.GetAtoms():
        symbol = atom.GetSymbol()
        for target in ATOM_SCAN_TARGETS.get(symbol, []):
            result = swap_atom_type(mol, atom.GetIdx(), target)
            if result is not None:
                candidates.append((Chem.MolToSmiles(result), f"atom_scan:{symbol}->{target}"))
    return candidates


def substituent_scan(mol, parent_smiles: str) -> list[tuple[str, str]]:
    candidates = []
    for atom_idx in open_aromatic_positions(mol):
        for group_name, frag_smiles in SUBSTITUENT_FRAGMENTS.items():
            result = attach_fragment(mol, atom_idx, frag_smiles)
            if result is not None:
                candidates.append((Chem.MolToSmiles(result), f"substituent_scan:{group_name}"))
    return candidates


def halogen_scan(mol, parent_smiles: str) -> list[tuple[str, str]]:
    candidates = []
    for atom in mol.GetAtoms():
        idx = atom.GetIdx()
        symbol = atom.GetSymbol()
        if atom.GetIsAromatic() and symbol == "C" and atom.GetTotalNumHs() > 0:
            for hal in ("F", "Cl", "Br"):
                result = attach_fragment(mol, idx, hal)
                if result is not None:
                    candidates.append((Chem.MolToSmiles(result), f"halogen_scan:add_{hal}"))
        elif symbol in HALOGENS:
            for hal in HALOGENS - {symbol}:
                result = swap_atom_type(mol, idx, hal)
                if result is not None:
                    candidates.append((Chem.MolToSmiles(result), f"halogen_scan:{symbol}->{hal}"))
    return candidates
