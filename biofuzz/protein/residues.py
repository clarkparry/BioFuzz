from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import DefaultDict

from biofuzz.protein.residue_keys import residue_key


def _is_hydrogen_atom(atom_name: str, atom_type: str) -> bool:
    normalized_name = atom_name.strip().upper()
    normalized_type = atom_type.strip().upper()
    return normalized_name.startswith("H") or normalized_type.startswith("H")


def parse_protein_residues(pdbqt_text: str) -> dict[str, list[tuple[float, float, float]]]:
    residues: DefaultDict[str, list[tuple[float, float, float]]] = defaultdict(list)

    for line in pdbqt_text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue

        atom_name = line[12:16].strip()
        atom_type = line[77:].strip()
        try:
            residue_id = int(line[22:26].strip())
            chain_id = line[21].strip()
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except ValueError:
            parts = line.split()
            if len(parts) < 9:
                continue
            try:
                atom_name = parts[2]
                chain_id = parts[4]
                residue_id = int(parts[5])
                x = float(parts[6])
                y = float(parts[7])
                z = float(parts[8])
                atom_type = parts[-1]
            except (ValueError, IndexError):
                continue

        if _is_hydrogen_atom(atom_name, atom_type):
            continue

        residues[residue_key(chain_id, residue_id)].append((x, y, z))

    return dict(residues)


def load_residue_coordinates(path: str | Path) -> dict[str, list[tuple[float, float, float]]]:
    text = Path(path).read_text(encoding="utf-8")
    return parse_protein_residues(text)
