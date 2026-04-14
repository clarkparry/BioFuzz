from __future__ import annotations

from dataclasses import dataclass
import random

try:
    from rdkit import Chem
except ImportError:  # pragma: no cover - handled by runtime behavior
    Chem = None  # type: ignore[assignment]

from biofuzz.molecules.filters import is_drug_like_mol


@dataclass(frozen=True)
class MutationCandidate:
    smiles: str
    stage: str
    mutation_type: str


def _sanitize_and_smiles(mol) -> tuple[object, str] | None:
    assert Chem is not None
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return None
    return mol, Chem.MolToSmiles(mol, canonical=True)


def atom_type_swap(mol, rng: random.Random):
    assert Chem is not None
    rw = Chem.RWMol(mol)

    candidate_map = {
        6: [7, 8],
        7: [6],
        16: [8],
    }

    candidates = [
        atom.GetIdx()
        for atom in rw.GetAtoms()
        if atom.GetAtomicNum() in candidate_map
    ]
    if not candidates:
        return None

    idx = rng.choice(candidates)
    atom = rw.GetAtomWithIdx(idx)
    atom.SetAtomicNum(rng.choice(candidate_map[atom.GetAtomicNum()]))
    return rw.GetMol()


def add_substituent(mol, rng: random.Random):
    assert Chem is not None
    rw = Chem.RWMol(mol)

    aromatic_sites = [
        atom.GetIdx()
        for atom in rw.GetAtoms()
        if atom.GetIsAromatic() and atom.GetTotalNumHs() > 0
    ]
    if not aromatic_sites:
        return None

    symbol = rng.choice(["F", "Cl", "C", "O"])
    target_idx = rng.choice(aromatic_sites)

    new_atom_idx = rw.AddAtom(Chem.Atom(symbol))
    rw.AddBond(target_idx, new_atom_idx, Chem.BondType.SINGLE)
    return rw.GetMol()


def remove_substituent(mol, rng: random.Random):
    assert Chem is not None
    rw = Chem.RWMol(mol)

    removable = []
    for atom in rw.GetAtoms():
        if atom.IsInRing() or atom.GetDegree() != 1:
            continue
        neighbor = atom.GetNeighbors()[0]
        if neighbor.IsInRing():
            removable.append(atom.GetIdx())

    if not removable:
        return None

    rw.RemoveAtom(rng.choice(removable))
    return rw.GetMol()


def mutate(
    smiles: str,
    n: int = 20,
    seed: int | None = None,
    min_mw: float = 0.0,
    max_mw: float = 550.0,
    max_logp: float = 5.0,
    max_hbd: int = 5,
    max_hba: int = 10,
    max_rot_bonds: int = 10,
) -> list[str]:
    return [
        candidate.smiles
        for candidate in mutate_with_metadata(
            smiles,
            n=n,
            seed=seed,
            min_mw=min_mw,
            max_mw=max_mw,
            max_logp=max_logp,
            max_hbd=max_hbd,
            max_hba=max_hba,
            max_rot_bonds=max_rot_bonds,
        )
    ]


def mutate_with_metadata(
    smiles: str,
    n: int = 20,
    seed: int | None = None,
    min_mw: float = 0.0,
    max_mw: float = 550.0,
    max_logp: float = 5.0,
    max_hbd: int = 5,
    max_hba: int = 10,
    max_rot_bonds: int = 10,
) -> list[MutationCandidate]:
    if n <= 0 or Chem is None:
        return []

    base = Chem.MolFromSmiles(smiles)
    if base is None:
        return []

    rng = random.Random(seed)
    start_smiles = Chem.MolToSmiles(base, canonical=True)

    operations = [
        ("atom_type_swap", atom_type_swap),
        ("add_substituent", add_substituent),
        ("remove_substituent", remove_substituent),
    ]
    mutants: dict[str, MutationCandidate] = {}

    attempts = 0
    max_attempts = max(100, n * 30)

    while len(mutants) < n and attempts < max_attempts:
        attempts += 1
        mutation_type, op = rng.choice(operations)
        working = Chem.Mol(base)
        mutated = op(working, rng)
        if mutated is None:
            continue

        sanitized = _sanitize_and_smiles(mutated)
        if sanitized is None:
            continue
        sanitized_mol, candidate = sanitized
        if candidate == start_smiles:
            continue

        if not is_drug_like_mol(
            sanitized_mol,
            min_mw=min_mw,
            max_mw=max_mw,
            max_logp=max_logp,
            max_hbd=max_hbd,
            max_hba=max_hba,
            max_rot_bonds=max_rot_bonds,
        ):
            continue

        mutants.setdefault(
            candidate,
            MutationCandidate(
                smiles=candidate,
                stage="havoc",
                mutation_type=mutation_type,
            ),
        )

    return [mutants[key] for key in sorted(mutants)]
