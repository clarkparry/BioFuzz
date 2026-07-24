from __future__ import annotations

import re
from dataclasses import dataclass

from rdkit import Chem, RDLogger

from biofuzz.oracle.scoring import ModeScore, best_mode, ligand_efficiency

RDLogger.DisableLog("rdApp.*")

# Legacy strain source. gnina does not actually emit an INTRA/strain REMARK in
# its pose output -- the intramolecular energy lives in the log's mode table
# instead (DockingMode.intramol). This regex is kept as a fallback for pose
# files from other engines that do annotate strain, but the table is the real
# source; relying on this alone is why every finding in the reference campaign
# recorded strain=null and the strain tier never gated anything.
_STRAIN_RE = re.compile(
    r"REMARK\s+(?:GNINA\s+)?(?:INTRA|strain)[A-Za-z]*\s*[:=]?\s*(-?\d+\.?\d*)",
    re.IGNORECASE,
)


@dataclass
class OracleConfig:
    affinity_threshold: float
    strain_threshold: float
    # Scoring policy fed to biofuzz.oracle.scoring: vina | cnn | consensus.
    scoring_policy: str = "consensus"
    # LE floor in kcal/mol per heavy atom. None disables the tier. This is the
    # tier that stops raw affinity from selecting for sheer molecular size.
    #
    # 0.22, not the textbook 0.3: a fixed drug-likeness default chosen so the gate
    # does not reject whole classes of real drug -- peptidomimetics like indinavir
    # and nirmatrelvir are legitimately LE-poor (~0.25). Reference-free at runtime;
    # it does not consult any known inhibitor of the target being fuzzed.
    min_ligand_efficiency: float | None = 0.22
    # Minimum gnina CNN pose confidence in [0, 1]. None disables the tier.
    min_cnn_pose_score: float | None = 0.4
    # Max allowed |vina - CNN| disagreement in kcal/mol. None disables the tier.
    max_score_disagreement: float | None = None
    # Minimum number of the target's essential (structure-derived) pocket
    # residues the pose must contact. None disables the tier. The essential set
    # and the per-pose contact count are computed by the campaign, which owns the
    # receptor geometry; the oracle only compares the count it is handed against
    # this floor, so it stays a pure function of its inputs. See
    # biofuzz/protein/essential.py and docs/modules/oracle.md "Tier 6".
    min_essential_contacts: int | None = 1


@dataclass
class OracleVerdict:
    is_hit: bool
    affinity: float
    strain: float | None
    passed_tiers: list[str]
    notes: str
    ligand_efficiency: float | None = None
    heavy_atom_count: int | None = None
    vina_affinity: float | None = None
    cnn_affinity_kcal: float | None = None
    cnn_pose_score: float | None = None
    scoring_policy: str | None = None
    essential_contacts: int | None = None


def extract_strain(pose_pdbqt: str) -> float | None:
    match = _STRAIN_RE.search(pose_pdbqt)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _heavy_atom_count(smiles: str | None) -> int | None:
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return mol.GetNumHeavyAtoms()


def _no_modes_verdict(policy: str) -> OracleVerdict:
    return OracleVerdict(
        is_hit=False,
        affinity=0.0,
        strain=None,
        passed_tiers=[],
        notes="no docking modes",
        scoring_policy=policy,
    )


def evaluate(
    modes: list,
    pose_pdbqt: str,
    oracle_config: OracleConfig,
    smiles: str | None = None,
    heavy_atom_count: int | None = None,
    essential_contacts: int | None = None,
) -> OracleVerdict:
    """Gate a docking result into hit / no-hit.

    `smiles` (or a precomputed `heavy_atom_count`) enables the ligand-efficiency
    tier. Without either, LE cannot be computed and that tier is skipped rather
    than failed, so callers that don't have the structure still work.

    `essential_contacts` is how many of the target's essential pocket residues
    this pose touches, computed by the caller (which owns the receptor geometry).
    Passing None -- because the tier is disabled, or no trustworthy essential set
    exists yet -- skips the tier rather than failing it, keeping evaluate() a pure
    function of the numbers it is handed.
    """
    policy = oracle_config.scoring_policy
    if not modes:
        return _no_modes_verdict(policy)

    mode, scored = best_mode(modes, policy)
    if mode is None or scored is None:
        return _no_modes_verdict(policy)

    affinity = scored.score

    # Prefer the table's intramolecular energy; fall back to a pose REMARK.
    strain = scored.intramol
    if strain is None:
        strain = extract_strain(pose_pdbqt)

    if heavy_atom_count is None:
        heavy_atom_count = _heavy_atom_count(smiles)
    le = (
        ligand_efficiency(affinity, heavy_atom_count)
        if heavy_atom_count is not None
        else None
    )

    passed_tiers: list[str] = []
    failure_reasons: list[str] = []

    if affinity <= oracle_config.affinity_threshold:
        passed_tiers.append("affinity")
    else:
        failure_reasons.append(
            f"affinity {affinity:.2f} > threshold {oracle_config.affinity_threshold}"
        )

    if strain is not None:
        if strain <= oracle_config.strain_threshold:
            passed_tiers.append("strain")
        else:
            failure_reasons.append(
                f"strain {strain:.2f} > threshold {oracle_config.strain_threshold}"
            )

    if oracle_config.min_ligand_efficiency is not None and le is not None:
        if le >= oracle_config.min_ligand_efficiency:
            passed_tiers.append("ligand_efficiency")
        else:
            failure_reasons.append(
                f"ligand efficiency {le:.3f} < minimum {oracle_config.min_ligand_efficiency}"
            )

    if oracle_config.min_cnn_pose_score is not None and scored.cnn_pose_score is not None:
        if scored.cnn_pose_score >= oracle_config.min_cnn_pose_score:
            passed_tiers.append("cnn_pose_score")
        else:
            failure_reasons.append(
                f"CNN pose score {scored.cnn_pose_score:.3f} "
                f"< minimum {oracle_config.min_cnn_pose_score}"
            )

    if oracle_config.max_score_disagreement is not None:
        disagreement = scored.disagreement
        if disagreement is not None:
            if disagreement <= oracle_config.max_score_disagreement:
                passed_tiers.append("score_agreement")
            else:
                failure_reasons.append(
                    f"vina/CNN disagreement {disagreement:.2f} kcal/mol "
                    f"> maximum {oracle_config.max_score_disagreement}"
                )

    if oracle_config.min_essential_contacts is not None and essential_contacts is not None:
        if essential_contacts >= oracle_config.min_essential_contacts:
            passed_tiers.append("essential_contact")
        else:
            failure_reasons.append(
                f"essential-residue contacts {essential_contacts} "
                f"< minimum {oracle_config.min_essential_contacts}"
            )

    is_hit = not failure_reasons
    notes = "passed" if is_hit else "; ".join(failure_reasons)

    return OracleVerdict(
        is_hit=is_hit,
        affinity=affinity,
        strain=strain,
        passed_tiers=passed_tiers,
        notes=notes,
        ligand_efficiency=le,
        heavy_atom_count=heavy_atom_count,
        vina_affinity=scored.vina_affinity,
        cnn_affinity_kcal=scored.cnn_affinity_kcal,
        cnn_pose_score=scored.cnn_pose_score,
        scoring_policy=policy,
        essential_contacts=essential_contacts,
    )


__all__ = ["OracleConfig", "OracleVerdict", "evaluate", "extract_strain", "ModeScore"]
