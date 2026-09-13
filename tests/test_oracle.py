from biofuzz.docker.parser import DockingMode
from biofuzz.oracle import OracleConfig, evaluate

CFG = OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5)


def test_strong_binder_low_strain_is_hit():
    hit_modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    hit_pose = "REMARK VINA RESULT: -10.5 ...\nREMARK GNINA INTRA ...\n"
    verdict = evaluate(hit_modes, hit_pose, CFG)
    assert verdict.is_hit
    assert "affinity" in verdict.passed_tiers


def test_weak_binder_no_hit():
    weak_modes = [DockingMode(mode=1, affinity=-6.0, rmsd_lb=0.0, rmsd_ub=0.0)]
    hit_pose = "REMARK VINA RESULT: -10.5 ...\nREMARK GNINA INTRA ...\n"
    assert not evaluate(weak_modes, hit_pose, CFG).is_hit


def test_high_strain_no_hit():
    hit_modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    high_strain_pose = "REMARK strain 8.2\n"
    assert not evaluate(hit_modes, high_strain_pose, CFG).is_hit


def test_missing_strain_annotation_still_a_hit():
    hit_modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    no_strain_pose = "REMARK VINA RESULT: -10.5\n"
    verdict = evaluate(hit_modes, no_strain_pose, CFG)
    assert verdict.is_hit
    assert verdict.strain is None


def test_low_strain_passes_strain_tier():
    hit_modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    low_strain_pose = "REMARK strain 1.2\n"
    verdict = evaluate(hit_modes, low_strain_pose, CFG)
    assert verdict.is_hit
    assert verdict.strain == 1.2
    assert "strain" in verdict.passed_tiers


def test_arbitrary_line_containing_word_strain_not_matched():
    hit_modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    misleading_pose = "REMARK this pose caused some strain on the algorithm\n"
    verdict = evaluate(hit_modes, misleading_pose, CFG)
    assert verdict.strain is None
    assert verdict.is_hit


def test_no_modes_is_not_a_hit():
    verdict = evaluate([], "REMARK\n", CFG)
    assert not verdict.is_hit


# --- Tiers beyond the affinity gate ---


def test_strain_comes_from_the_intramol_column_not_a_pose_remark():
    """gnina reports strain in its table, never as a pose REMARK.

    The oracle only looked for a REMARK, so every finding in the reference
    campaign recorded strain=null and the strain tier gated nothing.
    """
    modes = [DockingMode(mode=1, affinity=-10.5, intramol=8.2, cnn_affinity=None)]
    verdict = evaluate(modes, "REMARK no strain annotation here\n", CFG)
    assert verdict.strain == 8.2
    assert not verdict.is_hit
    assert "strain" not in verdict.passed_tiers


def test_low_intramol_passes_strain_tier():
    modes = [DockingMode(mode=1, affinity=-10.5, intramol=1.2)]
    verdict = evaluate(modes, "", CFG)
    assert verdict.strain == 1.2
    assert verdict.is_hit
    assert "strain" in verdict.passed_tiers


def test_ligand_efficiency_tier_rejects_oversized_binder():
    cfg = OracleConfig(
        affinity_threshold=-9.0, strain_threshold=3.5, min_ligand_efficiency=0.30
    )
    # Sildenafil-sized molecule scraping past the affinity gate on bulk alone.
    big = "CCCc1nn(C)c2c(=O)[nH]c(-c3cc(S(=O)(=O)N4CCN(C)CC4)ccc3OCC)nc12"
    modes = [DockingMode(mode=1, affinity=-9.5)]
    verdict = evaluate(modes, "", cfg, smiles=big)

    assert "affinity" in verdict.passed_tiers
    assert not verdict.is_hit  # ... but fails on efficiency
    assert "ligand efficiency" in verdict.notes


def test_ligand_efficiency_tier_skipped_without_structure():
    """No SMILES means LE can't be computed -- skip the tier, don't fail it."""
    cfg = OracleConfig(
        affinity_threshold=-9.0, strain_threshold=3.5, min_ligand_efficiency=0.30
    )
    verdict = evaluate([DockingMode(mode=1, affinity=-10.5)], "", cfg)
    assert verdict.is_hit
    assert verdict.ligand_efficiency is None


def test_ligand_efficiency_tier_disabled_by_none():
    cfg = OracleConfig(
        affinity_threshold=-9.0, strain_threshold=3.5, min_ligand_efficiency=None
    )
    modes = [DockingMode(mode=1, affinity=-9.5)]
    verdict = evaluate(modes, "", cfg, smiles="c1ccccc1" * 3)
    assert verdict.is_hit


def test_cnn_pose_score_tier_rejects_low_confidence_pose():
    """The sharpest available tier.

    Measured through this pipeline the five bundled reference drugs score
    0.60-0.98, while poses from unconstrained mutant chemistry commonly land
    around 0.1-0.3, so the default floor of 0.4 separates them.
    """
    cfg = OracleConfig(
        affinity_threshold=-9.0,
        strain_threshold=3.5,
        min_cnn_pose_score=0.4,
        min_ligand_efficiency=None,
    )
    unconvincing = [DockingMode(mode=1, affinity=-10.5, cnn_pose_score=0.32, cnn_affinity=9.0)]
    assert not evaluate(unconvincing, "", cfg).is_hit

    convincing = [DockingMode(mode=1, affinity=-10.5, cnn_pose_score=0.93, cnn_affinity=9.0)]
    assert evaluate(convincing, "", cfg).is_hit


def test_consensus_policy_rejects_what_vina_alone_would_accept():
    """The scoring fix, end to end: vina says -11, the CNN disagrees."""
    cfg = OracleConfig(
        affinity_threshold=-10.0,
        strain_threshold=3.5,
        scoring_policy="consensus",
        min_ligand_efficiency=None,
        min_cnn_pose_score=None,
    )
    # CNN pKd 5.0 -> about -6.8 kcal/mol.
    modes = [DockingMode(mode=1, affinity=-11.0, cnn_affinity=5.0)]

    assert not evaluate(modes, "", cfg).is_hit

    vina_only = OracleConfig(
        affinity_threshold=-10.0,
        strain_threshold=3.5,
        scoring_policy="vina",
        min_ligand_efficiency=None,
        min_cnn_pose_score=None,
    )
    assert evaluate(modes, "", vina_only).is_hit  # what the old oracle did


def test_verdict_carries_per_function_detail():
    cfg = OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5, min_ligand_efficiency=None)
    modes = [DockingMode(mode=1, affinity=-10.5, intramol=0.5, cnn_pose_score=0.9, cnn_affinity=8.0)]
    verdict = evaluate(modes, "", cfg, smiles="CCO")

    assert verdict.vina_affinity == -10.5
    assert verdict.cnn_affinity_kcal is not None
    assert verdict.cnn_pose_score == 0.9
    assert verdict.scoring_policy == "consensus"
    assert verdict.heavy_atom_count == 3


def test_essential_contact_tier_rejects_wrong_pocket_pose():
    """A pose can score well yet engage none of the must-touch residues."""
    cfg = OracleConfig(
        affinity_threshold=-9.0,
        strain_threshold=3.5,
        min_ligand_efficiency=None,
        min_cnn_pose_score=None,
        min_essential_contacts=1,
    )
    modes = [DockingMode(mode=1, affinity=-10.5)]

    missed = evaluate(modes, "", cfg, essential_contacts=0)
    assert not missed.is_hit
    assert "essential-residue contacts" in missed.notes

    engaged = evaluate(modes, "", cfg, essential_contacts=1)
    assert engaged.is_hit
    assert "essential_contact" in engaged.passed_tiers
    assert engaged.essential_contacts == 1


def test_essential_contact_tier_skipped_without_a_count():
    """No count (tier not gating for this target) -> skip, don't fail."""
    cfg = OracleConfig(
        affinity_threshold=-9.0,
        strain_threshold=3.5,
        min_ligand_efficiency=None,
        min_cnn_pose_score=None,
        min_essential_contacts=1,
    )
    verdict = evaluate([DockingMode(mode=1, affinity=-10.5)], "", cfg, essential_contacts=None)
    assert verdict.is_hit
    assert "essential_contact" not in verdict.passed_tiers


def test_essential_contact_tier_disabled_by_none():
    cfg = OracleConfig(
        affinity_threshold=-9.0,
        strain_threshold=3.5,
        min_ligand_efficiency=None,
        min_cnn_pose_score=None,
        min_essential_contacts=None,
    )
    verdict = evaluate([DockingMode(mode=1, affinity=-10.5)], "", cfg, essential_contacts=0)
    assert verdict.is_hit


def test_max_score_disagreement_tier():
    cfg = OracleConfig(
        affinity_threshold=-9.0,
        strain_threshold=3.5,
        scoring_policy="vina",
        min_ligand_efficiency=None,
        min_cnn_pose_score=None,
        max_score_disagreement=2.0,
    )
    # vina -12.0 vs CNN pKd 5.0 (~-6.8): a 5+ kcal/mol conflict.
    conflicted = [DockingMode(mode=1, affinity=-12.0, cnn_affinity=5.0)]
    verdict = evaluate(conflicted, "", cfg)
    assert not verdict.is_hit
    assert "disagreement" in verdict.notes
