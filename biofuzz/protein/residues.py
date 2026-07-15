from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProteinAtom:
    x: float
    y: float
    z: float
    type: str


def residue_key(chain: str, res_seq: str) -> str:
    return f"{chain}:{res_seq}"


def parse_receptor_residues(
    pdbqt_path: str, residue_ids: set[str] | None = None
) -> dict[str, list[ProteinAtom]]:
    residues: dict[str, list[ProteinAtom]] = {}
    with open(pdbqt_path) as fh:
        for line in fh:
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue
            tokens = line.split()
            if len(tokens) < 13:
                continue
            atom_type = tokens[-1]
            if atom_type.upper().startswith("H"):
                continue

            chain = tokens[4]
            res_seq = tokens[5]
            rid = residue_key(chain, res_seq)
            if residue_ids is not None and rid not in residue_ids:
                continue

            x, y, z = float(tokens[6]), float(tokens[7]), float(tokens[8])
            residues.setdefault(rid, []).append(ProteinAtom(x=x, y=y, z=z, type=atom_type))

    return residues
