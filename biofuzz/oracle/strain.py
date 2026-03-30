from __future__ import annotations

import re


_STRAIN_PATTERNS = [
    re.compile(r"strain[^\d+\-]*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"intra[^\d+\-]*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE),
]


def parse_strain_energy(pose_pdbqt: str) -> float | None:
    for line in pose_pdbqt.splitlines():
        if not line.startswith("REMARK"):
            continue
        for pattern in _STRAIN_PATTERNS:
            match = pattern.search(line)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
    return None


def passes_strain(pose_pdbqt: str, threshold: float = 3.5) -> bool:
    strain = parse_strain_energy(pose_pdbqt)
    if strain is None:
        # Treat missing strain annotations as pass; not all engines emit this signal.
        return True
    return strain <= threshold
