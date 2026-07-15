from __future__ import annotations

from biofuzz.mutator import ops

_ATOM_TYPE_SWAP_TARGETS = {"C": ["N", "O"], "N": ["C"], "S": ["O"]}


def _atom_type_swap(mol, rng):
    positions = [a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() in _ATOM_TYPE_SWAP_TARGETS]
    if not positions:
        return None
    idx = rng.choice(positions)
    symbol = mol.GetAtomWithIdx(idx).GetSymbol()
    targets = _ATOM_TYPE_SWAP_TARGETS.get(symbol, [])
    if not targets:
        return None
    return ops.swap_atom_type(mol, idx, rng.choice(targets))


_ALL_HAVOC_OPS = [
    _atom_type_swap,
    ops.add_random_substituent,
    ops.remove_terminal_substituent,
    ops.linker_extend,
    ops.linker_contract,
    ops.ring_atom_swap_to_nitrogen,
    ops.bioisostere_replace,
    ops.ring_open,
    ops.ring_close,
]


def havoc_mutate(mol, rng):
    n_ops = rng.randint(1, 8)
    current = mol
    applied: list[str] = []
    for _ in range(n_ops):
        op = rng.choice(_ALL_HAVOC_OPS)
        result = op(current, rng)
        if result is None:
            continue
        current = result
        applied.append(op.__name__)
    if not applied:
        return None, []
    return current, applied
