from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from biofuzz.docking.config import PocketConfig
from biofuzz.docking.parser import PoseAtom
from biofuzz.protein.residue_keys import normalize_residue_keys


def _distance_sq(
    lhs: tuple[float, float, float],
    rhs: tuple[float, float, float],
) -> float:
    dx = lhs[0] - rhs[0]
    dy = lhs[1] - rhs[1]
    dz = lhs[2] - rhs[2]
    return dx * dx + dy * dy + dz * dz


def _is_hydrogen(atom: PoseAtom) -> bool:
    atom_type = atom.type.strip().upper()
    atom_name = atom.name.strip().upper()
    return atom_type.startswith("H") or atom_name.startswith("H")


def compute_fingerprint(
    pose_atoms: Sequence[PoseAtom],
    protein_residues: Mapping[str, Sequence[tuple[float, float, float]]],
    pocket: PocketConfig | Iterable[str | int],
    contact_cutoff: float | None = None,
) -> frozenset[str]:
    if isinstance(pocket, PocketConfig):
        residue_ids = pocket.residue_ids
        cutoff = contact_cutoff if contact_cutoff is not None else pocket.contact_cutoff
    else:
        residue_ids = normalize_residue_keys(pocket)
        cutoff = 3.5 if contact_cutoff is None else float(contact_cutoff)

    if not pose_atoms:
        return frozenset()

    cutoff_sq = cutoff * cutoff
    contacted: set[str] = set()

    atom_coords = [
        (atom.x, atom.y, atom.z)
        for atom in pose_atoms
        if not _is_hydrogen(atom)
    ]
    if not atom_coords:
        return frozenset()

    for residue_id in residue_ids:
        coords = protein_residues.get(residue_id)
        if not coords:
            continue

        found_contact = False
        for residue_coord in coords:
            for ligand_coord in atom_coords:
                if _distance_sq(residue_coord, ligand_coord) <= cutoff_sq:
                    contacted.add(residue_id)
                    found_contact = True
                    break
            if found_contact:
                break

    return frozenset(contacted)
