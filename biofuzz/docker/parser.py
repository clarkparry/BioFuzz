from __future__ import annotations

import re
from dataclasses import dataclass

# gnina's mode table has two shapes depending on --cnn_scoring.
#
# With CNN scoring on (the default, and what the fuzzer runs):
#     mode |  affinity  |  intramol  |    CNN     |   CNN
#          | (kcal/mol) | (kcal/mol) | pose score | affinity
#     -----+------------+------------+------------+----------
#         1       -9.81        1.30       0.3207      6.795
#
# With --cnn_scoring=none it degrades to vina's table:
#     mode |  affinity | rmsd l.b.| rmsd u.b.
#         1      -9.81       0.000     0.000
#
# Both are matched here: the fifth float is optional, and its presence is what
# distinguishes the two layouts. The previous regex required exactly five
# floats and read only the second, which silently discarded the intramolecular
# energy and both CNN columns and failed to parse non-CNN runs at all.
_MODE_LINE_RE = re.compile(
    r"^[ \t]*(\d+)"
    r"[ \t]+(-?\d+\.\d+)"
    r"[ \t]+(-?\d+\.\d+)"
    r"[ \t]+(-?\d+\.\d+)"
    r"(?:[ \t]+(-?\d+\.\d+))?"
    r"[ \t]*$",
    re.MULTILINE,
)


@dataclass
class DockingMode:
    mode: int
    affinity: float
    rmsd_lb: float = 0.0
    rmsd_ub: float = 0.0
    # Intramolecular energy of the docked conformer (kcal/mol). This is the
    # strain signal the oracle gates on -- gnina reports it in the table, not
    # as a pose REMARK.
    intramol: float | None = None
    # CNN pose score in [0, 1]: the network's confidence that the pose is a
    # real binding mode (not a measure of potency).
    cnn_pose_score: float | None = None
    # CNN-predicted binding affinity in pKd units (higher = tighter).
    cnn_affinity: float | None = None


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
        third, fourth, fifth = match.group(3), match.group(4), match.group(5)

        if fifth is None:
            # vina layout: affinity | rmsd l.b. | rmsd u.b.
            modes.append(
                DockingMode(
                    mode=mode_num,
                    affinity=affinity,
                    rmsd_lb=float(third),
                    rmsd_ub=float(fourth),
                )
            )
        else:
            # gnina CNN layout: affinity | intramol | CNN pose score | CNN affinity
            modes.append(
                DockingMode(
                    mode=mode_num,
                    affinity=affinity,
                    intramol=float(third),
                    cnn_pose_score=float(fourth),
                    cnn_affinity=float(fifth),
                )
            )
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
