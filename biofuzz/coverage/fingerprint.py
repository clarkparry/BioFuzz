from __future__ import annotations

import math

# AutoDock atom-type heuristics for the optional interaction-type extension.
# Note: pose_atoms and protein_residues are heavy-atom-only (hydrogens are
# stripped upstream by biofuzz.docker.parser / biofuzz.protein.residues), so
# true donor-vs-acceptor character (which depends on attached H position)
# can't be determined exactly. This is a documented simplification: atoms
# with an explicit AutoDock acceptor type are treated as acceptors, other
# polar heavy atoms are treated as (possible) donors.
_ACCEPTOR_TYPES = {"OA", "NA", "SA"}
_POLAR_TYPES_PREFIXES = ("N", "O", "S")
_HYDROPHOBIC_TYPES = {"C", "A"}

_HBOND_CUTOFF = 3.5
_HYDROPHOBIC_CUTOFF = 4.0


def _distance(a, b) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _is_acceptor(atom_type: str) -> bool:
    return atom_type.upper() in _ACCEPTOR_TYPES


def _is_polar(atom_type: str) -> bool:
    return atom_type.upper().startswith(_POLAR_TYPES_PREFIXES)


def _is_hydrophobic(atom_type: str) -> bool:
    return atom_type.upper() in _HYDROPHOBIC_TYPES


def build_fingerprint(
    pose_atoms,
    protein_residues: dict[str, list],
    contact_cutoff: float = 3.5,
    interaction_types_enabled: bool = False,
) -> frozenset[str]:
    fingerprint: set[str] = set()

    for residue_id, residue_atoms in protein_residues.items():
        contacted = False
        types_present: set[str] = set()

        for ligand_atom in pose_atoms:
            for residue_atom in residue_atoms:
                dist = _distance(ligand_atom, residue_atom)
                if dist <= contact_cutoff:
                    contacted = True

                if interaction_types_enabled:
                    if dist <= _HBOND_CUTOFF:
                        if _is_acceptor(ligand_atom.type):
                            types_present.add("hbond_acceptor")
                        elif _is_polar(ligand_atom.type):
                            types_present.add("hbond_donor")
                    if dist <= _HYDROPHOBIC_CUTOFF and _is_hydrophobic(
                        ligand_atom.type
                    ) and _is_hydrophobic(residue_atom.type):
                        types_present.add("hydrophobic")

        if not contacted:
            continue

        if interaction_types_enabled and types_present:
            for itype in types_present:
                fingerprint.add(f"{residue_id}:{itype}")
        else:
            fingerprint.add(residue_id)

    return frozenset(fingerprint)
