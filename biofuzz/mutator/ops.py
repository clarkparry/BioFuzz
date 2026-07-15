from __future__ import annotations

import random

from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem

RDLogger.DisableLog("rdApp.*")

ATOMIC_NUM = {"C": 6, "N": 7, "O": 8, "S": 16, "F": 9, "Cl": 17, "Br": 35, "I": 53}
HALOGENS = {"F", "Cl", "Br", "I"}

SUBSTITUENT_FRAGMENTS = {
    "F": "F",
    "Cl": "Cl",
    "Br": "Br",
    "CH3": "C",
    "OH": "O",
    "NH2": "N",
    "CN": "C#N",
    "CF3": "C(F)(F)F",
    "OCH3": "OC",
    "COOH": "C(=O)O",
}


def _sanitized(mol) -> Chem.Mol | None:
    try:
        mol = mol.GetMol() if isinstance(mol, Chem.RWMol) else mol
        Chem.SanitizeMol(mol)
        return mol
    except Exception:
        return None


def open_positions(mol) -> list[int]:
    return [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetTotalNumHs() > 0]


def open_aromatic_positions(mol) -> list[int]:
    return [
        atom.GetIdx()
        for atom in mol.GetAtoms()
        if atom.GetIsAromatic() and atom.GetTotalNumHs() > 0
    ]


def attach_fragment(mol, atom_idx: int, frag_smiles: str) -> Chem.Mol | None:
    frag = Chem.MolFromSmiles(frag_smiles)
    if frag is None:
        return None
    combined = Chem.RWMol(Chem.CombineMols(mol, frag))
    attach_idx = mol.GetNumAtoms()
    try:
        combined.AddBond(atom_idx, attach_idx, Chem.BondType.SINGLE)
    except Exception:
        return None
    return _sanitized(combined)


def add_random_substituent(mol, rng: random.Random) -> Chem.Mol | None:
    positions = open_positions(mol)
    if not positions:
        return None
    atom_idx = rng.choice(positions)
    group_name = rng.choice(list(SUBSTITUENT_FRAGMENTS))
    return attach_fragment(mol, atom_idx, SUBSTITUENT_FRAGMENTS[group_name])


def swap_atom_type(mol, atom_idx: int, target_element: str) -> Chem.Mol | None:
    rw = Chem.RWMol(mol)
    try:
        rw.GetAtomWithIdx(atom_idx).SetAtomicNum(ATOMIC_NUM[target_element])
    except Exception:
        return None
    return _sanitized(rw)


def remove_terminal_substituent(mol, rng: random.Random) -> Chem.Mol | None:
    candidates = [
        atom.GetIdx()
        for atom in mol.GetAtoms()
        if atom.GetDegree() == 1 and not atom.GetIsAromatic() and not atom.IsInRing()
    ]
    non_ring_candidates = [
        idx
        for idx in candidates
        if mol.GetAtomWithIdx(idx).GetNeighbors()
        and mol.GetAtomWithIdx(idx).GetNeighbors()[0].IsInRing()
    ]
    pool = non_ring_candidates or candidates
    if not pool:
        return None
    atom_idx = rng.choice(pool)
    rw = Chem.RWMol(mol)
    rw.RemoveAtom(atom_idx)
    return _sanitized(rw)


def linker_extend(mol, rng: random.Random) -> Chem.Mol | None:
    chain_bonds = [
        bond
        for bond in mol.GetBonds()
        if bond.GetBondType() == Chem.BondType.SINGLE
        and not bond.IsInRing()
        and not bond.GetBeginAtom().GetIsAromatic()
        and not bond.GetEndAtom().GetIsAromatic()
    ]
    if not chain_bonds:
        return None
    bond = rng.choice(chain_bonds)
    a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
    rw = Chem.RWMol(mol)
    rw.RemoveBond(a, b)
    new_idx = rw.AddAtom(Chem.Atom(6))
    rw.AddBond(a, new_idx, Chem.BondType.SINGLE)
    rw.AddBond(new_idx, b, Chem.BondType.SINGLE)
    return _sanitized(rw)


def linker_contract(mol, rng: random.Random) -> Chem.Mol | None:
    candidates = [
        atom.GetIdx()
        for atom in mol.GetAtoms()
        if atom.GetSymbol() == "C"
        and not atom.GetIsAromatic()
        and not atom.IsInRing()
        and atom.GetDegree() == 2
    ]
    if not candidates:
        return None
    atom_idx = rng.choice(candidates)
    atom = mol.GetAtomWithIdx(atom_idx)
    neighbors = [n.GetIdx() for n in atom.GetNeighbors()]
    if len(neighbors) != 2:
        return None
    rw = Chem.RWMol(mol)
    rw.RemoveAtom(atom_idx)
    n0, n1 = neighbors
    if n0 > atom_idx:
        n0 -= 1
    if n1 > atom_idx:
        n1 -= 1
    try:
        rw.AddBond(n0, n1, Chem.BondType.SINGLE)
    except Exception:
        return None
    return _sanitized(rw)


def ring_atom_swap_to_nitrogen(mol, rng: random.Random) -> Chem.Mol | None:
    candidates = [
        atom.GetIdx()
        for atom in mol.GetAtoms()
        if atom.GetSymbol() == "C" and atom.IsInRing()
    ]
    if not candidates:
        return None
    atom_idx = rng.choice(candidates)
    return swap_atom_type(mol, atom_idx, "N")


def ring_open(mol, rng: random.Random) -> Chem.Mol | None:
    ring_bonds = [
        bond
        for bond in mol.GetBonds()
        if bond.IsInRing() and bond.GetBondType() == Chem.BondType.SINGLE and not bond.GetIsAromatic()
    ]
    if not ring_bonds:
        return None
    bond = rng.choice(ring_bonds)
    rw = Chem.RWMol(mol)
    rw.RemoveBond(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
    return _sanitized(rw)


def bioisostere_replace(mol, rng: random.Random) -> Chem.Mol | None:
    from biofuzz.mutator.library import BIOISOSTERE_PAIRS

    pairs = list(BIOISOSTERE_PAIRS)
    rng.shuffle(pairs)
    for smarts, repl_smiles in pairs:
        query = Chem.MolFromSmarts(smarts)
        repl = Chem.MolFromSmiles(repl_smiles)
        if query is None or repl is None:
            continue
        if not mol.HasSubstructMatch(query):
            continue
        try:
            products = Chem.ReplaceSubstructs(mol, query, repl, replaceAll=False)
        except Exception:
            continue
        for product in products:
            result = _sanitized(product)
            if result is not None:
                return result
    return None


def ring_close(mol, rng: random.Random) -> Chem.Mol | None:
    positions = open_positions(mol)
    if len(positions) < 2:
        return None
    for _ in range(10):
        a, b = rng.sample(positions, 2)
        if mol.GetBondBetweenAtoms(a, b) is not None:
            continue
        path = Chem.GetShortestPath(mol, a, b)
        ring_size = len(path)
        if ring_size < 4 or ring_size > 5:
            continue
        rw = Chem.RWMol(mol)
        try:
            rw.AddBond(a, b, Chem.BondType.SINGLE)
        except Exception:
            continue
        result = _sanitized(rw)
        if result is not None:
            return result
    return None
