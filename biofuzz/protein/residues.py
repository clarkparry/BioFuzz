from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import DefaultDict


def parse_protein_residues(pdbqt_text: str) -> dict[int, list[tuple[float, float, float]]]:
    residues: DefaultDict[int, list[tuple[float, float, float]]] = defaultdict(list)

    for line in pdbqt_text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue

        try:
            residue_id = int(line[22:26].strip())
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
        except ValueError:
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                residue_id = int(parts[5])
                x = float(parts[6])
                y = float(parts[7])
                z = float(parts[8])
            except (ValueError, IndexError):
                continue

        residues[residue_id].append((x, y, z))

    return dict(residues)


def load_residue_coordinates(path: str | Path) -> dict[int, list[tuple[float, float, float]]]:
    text = Path(path).read_text(encoding="utf-8")
    return parse_protein_residues(text)
