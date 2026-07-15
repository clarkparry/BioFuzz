from __future__ import annotations

import re
from dataclasses import dataclass

# gnina's default CNN-scoring mode table replaces vina's rmsd l.b./u.b. columns
# with CNN pose score / CNN affinity columns (verified against real gnina 1.3.2
# output). rmsd_lb/rmsd_ub aren't available from that table, so they default to
# 0.0 rather than being guessed.
_MODE_LINE_RE = re.compile(
    r"^\s*(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$",
    re.MULTILINE,
)


@dataclass
class DockingMode:
    mode: int
    affinity: float
    rmsd_lb: float
    rmsd_ub: float


@dataclass
class PoseAtom:
    name: str
    x: float
    y: float
    z: float
    charge: float
    type: str


def parse_log(log_text: str) -> list[DockingMode]:
    modes = []
    for match in _MODE_LINE_RE.finditer(log_text):
        mode_num = int(match.group(1))
        affinity = float(match.group(2))
        modes.append(DockingMode(mode=mode_num, affinity=affinity, rmsd_lb=0.0, rmsd_ub=0.0))
    return modes


def _parse_atom_lines(lines: list[str]) -> list[PoseAtom]:
    atoms = []
    for line in lines:
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        tokens = line.split()
        if len(tokens) < 12:
            continue
        name = tokens[2]
        atom_type = tokens[-1]
        if atom_type.upper().startswith("H"):
            continue
        charge = float(tokens[-2])
        x, y, z = float(tokens[-7]), float(tokens[-6]), float(tokens[-5])
        atoms.append(PoseAtom(name=name, x=x, y=y, z=z, charge=charge, type=atom_type))
    return atoms


def parse_all_poses(pdbqt_text: str) -> list[list[PoseAtom]]:
    lines = pdbqt_text.splitlines()
    if not any(line.startswith("MODEL") for line in lines):
        return [_parse_atom_lines(lines)]

    models: list[list[str]] = []
    current: list[str] = []
    in_model = False
    for line in lines:
        if line.startswith("MODEL"):
            in_model = True
            current = []
            continue
        if line.startswith("ENDMDL"):
            in_model = False
            models.append(current)
            continue
        if in_model:
            current.append(line)

    return [_parse_atom_lines(model_lines) for model_lines in models]


def parse_pose(pdbqt_text: str) -> list[PoseAtom]:
    poses = parse_all_poses(pdbqt_text)
    return poses[0] if poses else []
