from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class DockingMode:
    mode: int
    affinity: float
    rmsd_lb: float
    rmsd_ub: float


@dataclass(frozen=True)
class PoseAtom:
    name: str
    x: float
    y: float
    z: float
    charge: float
    type: str


_LOG_MODE_RE = re.compile(
    r"^\s*(?P<mode>\d+)\s+(?P<affinity>-?\d+(?:\.\d+)?)\s+"
    r"(?P<rmsd_lb>\d+(?:\.\d+)?)\s+(?P<rmsd_ub>\d+(?:\.\d+)?)\s*$"
)


def parse_log(log_text: str) -> list[DockingMode]:
    modes: list[DockingMode] = []
    for line in log_text.splitlines():
        match = _LOG_MODE_RE.match(line)
        if not match:
            continue
        modes.append(
            DockingMode(
                mode=int(match.group("mode")),
                affinity=float(match.group("affinity")),
                rmsd_lb=float(match.group("rmsd_lb")),
                rmsd_ub=float(match.group("rmsd_ub")),
            )
        )

    return sorted(modes, key=lambda item: item.mode)


def _parse_atom_line(line: str) -> PoseAtom | None:
    if not (line.startswith("ATOM") or line.startswith("HETATM")):
        return None

    # PDBQT is fixed-width, but some tools collapse spacing; support both styles.
    name = line[12:16].strip() or "X"

    try:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
        charge = float(line[70:76])
        atom_type = line[77:].strip() or "C"
        return PoseAtom(name=name, x=x, y=y, z=z, charge=charge, type=atom_type)
    except ValueError:
        parts = line.split()
        if len(parts) < 8:
            return None
        try:
            return PoseAtom(
                name=parts[2],
                x=float(parts[-7]),
                y=float(parts[-6]),
                z=float(parts[-5]),
                charge=float(parts[-2]),
                type=parts[-1],
            )
        except ValueError:
            return None


def parse_pose(pdbqt_text: str) -> list[PoseAtom]:
    atoms: list[PoseAtom] = []
    model_depth = 0

    for line in pdbqt_text.splitlines():
        if line.startswith("MODEL"):
            model_depth += 1
            if model_depth > 1:
                break
            continue

        if line.startswith("ENDMDL"):
            break

        atom = _parse_atom_line(line)
        if atom is not None:
            atoms.append(atom)

    return atoms
