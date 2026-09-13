"""Essential residues: the sub-pocket a genuine binder must engage.

An affinity number says *how tightly* a pose scores; it says nothing about
*where* it binds. A molecule can clear every scalar tier (affinity, strain,
ligand efficiency, CNN pose score) while sitting in the wrong corner of the
pocket, making none of the contacts that define the site. This module supplies
the missing geometric check: a small set of pocket residues that any real binder
has to touch, and a test of whether a docked pose touches them.

The whole point is that the set is derived **from the protein alone** -- never
from a known inhibitor, and without needing to know what the protein is. Three
signals compose into it (see docs/architecture.md):

  * **Static, structural (always available).** Score every pocket residue by how
    *buried* it is (deep residues are the anchor points a ligand must reach) and
    how *polar/ionizable* it is (a charged or polar side chain buried in a pocket
    is expensive to bury, so the protein put it there for a functional reason).
    On the bundled `hiv_protease` receptor this recovers the catalytic
    aspartate dyad (A:25 / B:25) as the top two residues, without either being
    named. It is a heuristic, not a catalytic-site predictor: on
    `sars_cov2_mpro` it ranks His41 fourth and does not surface Cys145 at all.
    Computed from the receptor at startup; `structural_essential_scores`.

  * **Static, ligandability (when a detector ran).** P2Rank already scores each
    pocket residue by druggability when a target is prepared. Those scores, if
    present, are blended into the structural ranking; see
    `tools/prepare_target_fixture.py`.

  * **Emergent, self-calibrating (accrues during a campaign).** BioFuzz docks its
    approved-drug seeds to calibrate them. The pocket residues that many
    *confident* seed poses all contact are, empirically, the ones this pocket
    demands -- learned with zero knowledge of the target. `EssentialResidues`
    accumulates that signal as seeds calibrate and unions it into the set.

The gate that consumes this is deliberately tolerant and self-disabling: a pose
need only engage ``min_contacts`` of the set (default 1), and if no trustworthy
set can be formed the tier reports nothing rather than rejecting on noise. This
module owns none of the docking or gating; it only ranks residues and counts
contacts, so it stays a pure, testable companion to the oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from biofuzz.protein.residues import ProteinAtom

# Heavy-atom elements that can donate or accept a hydrogen bond / carry formal
# charge on an amino-acid side chain (Asp/Glu carboxylates, Lys/Arg/His
# nitrogens, Ser/Thr/Tyr hydroxyls, Cys/Met sulfurs). The first character of an
# AutoDock atom type is its element, which is all the specificity we need here.
_POLAR_ELEMENTS = frozenset({"N", "O", "S"})

# Radius (A) within which surrounding protein heavy atoms count toward a
# residue's burial. ~4.5 A is one van der Waals contact shell; a residue with
# many neighbours in that shell sits deep in the fold rather than on the rim.
_BURIAL_RADIUS = 4.5


def _is_polar(atom: ProteinAtom) -> bool:
    return bool(atom.type) and atom.type[0].upper() in _POLAR_ELEMENTS


def _zscore(values: dict[str, float]) -> dict[str, float]:
    """Z-normalise a residue->value map so unlike signals can be summed.

    Returns zeros when the values carry no spread (n < 2 or zero variance),
    which makes that signal contribute nothing rather than dominating on scale.
    """
    if len(values) < 2:
        return {rid: 0.0 for rid in values}
    n = len(values)
    mean = sum(values.values()) / n
    var = sum((v - mean) ** 2 for v in values.values()) / n
    if var <= 0.0:
        return {rid: 0.0 for rid in values}
    std = var**0.5
    return {rid: (v - mean) / std for rid, v in values.items()}


def structural_essential_scores(
    all_residues: dict[str, list[ProteinAtom]],
    pocket_residue_ids,
    p2rank_scores: dict[str, float] | None = None,
    burial_radius: float = _BURIAL_RADIUS,
) -> list[tuple[str, float]]:
    """Rank pocket residues by how likely a binder must engage them.

    `all_residues` is the *whole* receptor (needed to measure burial); the
    ranking is restricted to `pocket_residue_ids`. `p2rank_scores`, when given,
    is a residue->ligandability map that is z-blended in as a third signal.

    Returns (residue_id, score) pairs sorted strongest-first. Residues absent
    from `all_residues` are skipped rather than scored on no atoms.
    """
    pocket = [rid for rid in pocket_residue_ids if all_residues.get(rid)]
    if not pocket:
        return []

    burial: dict[str, float] = {}
    polar: dict[str, float] = {}
    for rid in pocket:
        atoms = all_residues[rid]
        polar[rid] = float(sum(1 for a in atoms if _is_polar(a)))
        # Neighbour count from every other residue's heavy atoms within the
        # burial shell. Squared-distance compare avoids a sqrt per pair.
        r2 = burial_radius * burial_radius
        own = id(atoms)
        neighbours = 0
        for other_atoms in all_residues.values():
            if id(other_atoms) == own:
                continue
            for a in atoms:
                for b in other_atoms:
                    dx = a.x - b.x
                    dy = a.y - b.y
                    dz = a.z - b.z
                    if dx * dx + dy * dy + dz * dz <= r2:
                        neighbours += 1
        burial[rid] = float(neighbours)

    z_burial = _zscore(burial)
    z_polar = _zscore(polar)
    z_p2rank = _zscore({rid: p2rank_scores.get(rid, 0.0) for rid in pocket}) if p2rank_scores else {}

    scored = [
        (rid, z_burial[rid] + z_polar[rid] + z_p2rank.get(rid, 0.0))
        for rid in pocket
    ]
    scored.sort(key=lambda pair: (pair[1], burial[pair[0]]), reverse=True)
    return scored


def select_static_essential(
    all_residues: dict[str, list[ProteinAtom]],
    pocket_residue_ids,
    count: int,
    p2rank_scores: dict[str, float] | None = None,
) -> list[str]:
    """The top `count` structurally-essential pocket residue ids."""
    if count <= 0:
        return []
    scored = structural_essential_scores(all_residues, pocket_residue_ids, p2rank_scores)
    return [rid for rid, _ in scored[:count]]


@dataclass
class EssentialResidues:
    """Tracks the essential-residue set and tests poses against it.

    Operates purely on *contact sets* (the residue ids a pose touches, which the
    caller already computes for coverage) -- it never sees atoms or docking, so
    it is cheap to call per pose and trivial to test. The static set is fixed at
    construction; the emergent set grows as confident calibration poses are
    reported through `observe_calibration`.
    """

    static: set[str]
    min_contacts: int = 1
    # A pocket residue joins the emergent set once this fraction of confident
    # calibration poses have contacted it -- i.e. it is one that essentially
    # every credible known-drug-like binder in this pocket has to touch.
    emergent_fraction: float = 0.6
    # Emergent evidence is ignored until at least this many confident poses exist,
    # so one lucky seed can't define the anchor.
    emergent_min_poses: int = 3
    # CNN pose confidence a calibration pose needs before it votes. Below this the
    # pose isn't a credible binding mode, so where it sits is not evidence.
    emergent_min_pose_score: float = 0.6
    # Ceiling on the active set. A large set makes the tolerant "engage >= 1"
    # gate trivial; capping keeps it selective. Static residues are kept first,
    # then the best-supported emergent ones.
    max_active: int = 6

    _confident_poses: int = 0
    _contact_counts: dict[str, int] = field(default_factory=dict)

    def observe_calibration(self, contacted_residue_ids, cnn_pose_score: float | None) -> None:
        """Record a calibration (seed) pose's pocket contacts, if it is credible."""
        if cnn_pose_score is None or cnn_pose_score < self.emergent_min_pose_score:
            return
        self._confident_poses += 1
        for rid in contacted_residue_ids:
            self._contact_counts[rid] = self._contact_counts.get(rid, 0) + 1

    def emergent(self) -> set[str]:
        if self._confident_poses < self.emergent_min_poses:
            return set()
        threshold = self.emergent_fraction * self._confident_poses
        return {rid for rid, c in self._contact_counts.items() if c >= threshold}

    def active(self) -> set[str]:
        """The set actually gated on: static first, then best-supported emergent,
        capped at `max_active`."""
        active = set(self.static)
        if len(active) >= self.max_active:
            return active
        extra = sorted(
            (self.emergent() - active),
            key=lambda rid: self._contact_counts.get(rid, 0),
            reverse=True,
        )
        for rid in extra[: self.max_active - len(active)]:
            active.add(rid)
        return active

    def is_gating(self) -> bool:
        """Whether the tier should gate. False (skip, don't fail) when no
        trustworthy set has formed -- the garbage guard."""
        return bool(self.active())

    def engaged_count(self, contacted_residue_ids) -> int:
        """How many active essential residues this pose's contacts include."""
        return len(self.active() & set(contacted_residue_ids))

    # ---- checkpoint state (emergent tallies only; static is rebuilt at startup) ----

    def state_dict(self) -> dict:
        return {
            "confident_poses": self._confident_poses,
            "contact_counts": dict(self._contact_counts),
        }

    def load_state(self, data: dict) -> None:
        self._confident_poses = int(data.get("confident_poses", 0))
        self._contact_counts = {str(k): int(v) for k, v in data.get("contact_counts", {}).items()}


__all__ = [
    "EssentialResidues",
    "structural_essential_scores",
    "select_static_essential",
]
