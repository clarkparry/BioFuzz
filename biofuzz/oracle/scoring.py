"""Scoring policy: how a docking mode becomes the single number the oracle gates on.

gnina reports two independent estimates of binding strength per mode:

  * ``affinity``     -- vina's empirical function, kcal/mol (negative = tighter)
  * ``cnn_affinity`` -- the CNN's predicted pKd (positive = tighter)

They are in different units and disagree often. This module converts the CNN
prediction to kcal/mol so the two are comparable, then combines them under a
configurable policy. Everything downstream (oracle, corpus priority, findings,
triage) speaks kcal/mol.
"""

from __future__ import annotations

from dataclasses import dataclass

# dG = -RT ln(Ka) = -2.303 RT * pKd. At T = 298.15 K, 2.303 * R * T = 1.364
# kcal/mol per log unit, the standard conversion used to put a predicted pKd
# on the same kcal/mol scale as vina's score.
KCAL_PER_LOG_UNIT = 1.364

VALID_POLICIES = ("vina", "cnn", "consensus")


def pkd_to_kcal(pkd: float) -> float:
    """pKd (higher = tighter) -> binding free energy in kcal/mol (lower = tighter)."""
    return -KCAL_PER_LOG_UNIT * pkd


@dataclass
class ModeScore:
    """The scored view of one docking mode, all energies in kcal/mol."""

    score: float
    vina_affinity: float
    cnn_affinity_kcal: float | None
    cnn_pose_score: float | None
    intramol: float | None
    policy: str

    @property
    def disagreement(self) -> float | None:
        """|vina - cnn| in kcal/mol. Large values mean the two functions conflict."""
        if self.cnn_affinity_kcal is None:
            return None
        return abs(self.vina_affinity - self.cnn_affinity_kcal)


def score_mode(mode, policy: str = "consensus") -> ModeScore:
    """Reduce a DockingMode to a single kcal/mol score under `policy`.

    - ``vina``      -- vina's empirical score only (what BioFuzz used to do
                       implicitly, and the reason gnina's CNN was being paid
                       for but never used).
    - ``cnn``       -- the CNN prediction only, converted to kcal/mol.
    - ``consensus`` -- the *weaker* of the two (least negative). A molecule
                       only scores well if both functions agree it binds,
                       which is what suppresses each function's own
                       false positives.

    Falls back to vina when the run carried no CNN columns (--cnn_scoring=none).
    """
    if policy not in VALID_POLICIES:
        raise ValueError(f"unknown scoring policy: {policy!r} (expected one of {VALID_POLICIES})")

    vina = mode.affinity
    cnn_kcal = pkd_to_kcal(mode.cnn_affinity) if mode.cnn_affinity is not None else None

    if cnn_kcal is None or policy == "vina":
        score = vina
    elif policy == "cnn":
        score = cnn_kcal
    else:
        score = max(vina, cnn_kcal)

    return ModeScore(
        score=score,
        vina_affinity=vina,
        cnn_affinity_kcal=cnn_kcal,
        cnn_pose_score=mode.cnn_pose_score,
        intramol=mode.intramol,
        policy=policy,
    )


def best_mode(modes: list, policy: str = "consensus"):
    """Pick the mode with the strongest score under `policy`.

    gnina already sorts its output, but by whichever function it was told to
    rank with -- which is not necessarily the one we gate on. Re-selecting here
    keeps the reported hit consistent with the configured policy.
    """
    if not modes:
        return None, None
    scored = [(score_mode(m, policy), m) for m in modes]
    best_score, best = min(scored, key=lambda pair: pair[0].score)
    return best, best_score


def ligand_efficiency(affinity_kcal: float, heavy_atom_count: int) -> float | None:
    """LE = -dG / heavy atom count, in kcal/mol per heavy atom.

    Docking scores grow roughly linearly with molecular size, so gating on raw
    affinity alone systematically selects large, greasy molecules regardless of
    whether they make good contacts. LE normalises that out. ~0.3 is the
    conventional lower bound for a viable lead.
    """
    if heavy_atom_count <= 0:
        return None
    return -affinity_kcal / heavy_atom_count
