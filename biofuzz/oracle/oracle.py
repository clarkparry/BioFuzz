from __future__ import annotations

import re
from dataclasses import dataclass

_STRAIN_RE = re.compile(
    r"REMARK\s+(?:GNINA\s+)?(?:INTRA|strain)[A-Za-z]*\s*[:=]?\s*(-?\d+\.?\d*)",
    re.IGNORECASE,
)


@dataclass
class OracleConfig:
    affinity_threshold: float
    strain_threshold: float


@dataclass
class OracleVerdict:
    is_hit: bool
    affinity: float
    strain: float | None
    passed_tiers: list[str]
    notes: str


def extract_strain(pose_pdbqt: str) -> float | None:
    match = _STRAIN_RE.search(pose_pdbqt)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def evaluate(modes: list, pose_pdbqt: str, oracle_config: OracleConfig) -> OracleVerdict:
    if not modes:
        return OracleVerdict(
            is_hit=False, affinity=0.0, strain=None, passed_tiers=[], notes="no docking modes"
        )

    affinity = modes[0].affinity
    strain = extract_strain(pose_pdbqt)

    passed_tiers: list[str] = []
    failure_reasons: list[str] = []

    if affinity <= oracle_config.affinity_threshold:
        passed_tiers.append("affinity")
    else:
        failure_reasons.append(
            f"affinity {affinity} > threshold {oracle_config.affinity_threshold}"
        )

    if strain is not None:
        if strain <= oracle_config.strain_threshold:
            passed_tiers.append("strain")
        else:
            failure_reasons.append(f"strain {strain} > threshold {oracle_config.strain_threshold}")

    is_hit = not failure_reasons
    notes = "passed" if is_hit else "; ".join(failure_reasons)

    return OracleVerdict(
        is_hit=is_hit,
        affinity=affinity,
        strain=strain,
        passed_tiers=passed_tiers,
        notes=notes,
    )
