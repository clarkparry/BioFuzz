import os

from biofuzz.protein.essential import (
    EssentialResidues,
    select_static_essential,
    structural_essential_scores,
)
from biofuzz.protein.residues import ProteinAtom, parse_receptor_residues


# --- Route B: structure-only ranking -------------------------------------


def _buried_polar_vs_exposed_nonpolar():
    """A residue that is both buried and polar, and one that is neither."""
    buried_polar = [ProteinAtom(0.0, 0.0, 0.0, "OA"), ProteinAtom(0.0, 0.0, 1.0, "N")]
    exposed_nonpolar = [ProteinAtom(50.0, 50.0, 50.0, "C")]
    # A non-pocket filler residue packed around the buried one, giving it many
    # burial neighbours; nothing is near the exposed residue.
    neighbours = [ProteinAtom(dx, dy, 0.0, "C") for dx in (-2, 2) for dy in (-2, 2)]
    return {
        "A:1": buried_polar,
        "A:2": exposed_nonpolar,
        "A:99": neighbours,
    }


def test_structural_scoring_ranks_buried_polar_first():
    residues = _buried_polar_vs_exposed_nonpolar()
    scored = structural_essential_scores(residues, {"A:1", "A:2"})
    assert [rid for rid, _ in scored] == ["A:1", "A:2"]


def test_select_static_essential_takes_top_k():
    residues = _buried_polar_vs_exposed_nonpolar()
    assert select_static_essential(residues, {"A:1", "A:2"}, count=1) == ["A:1"]
    assert select_static_essential(residues, {"A:1", "A:2"}, count=0) == []


def test_structural_scoring_skips_residues_with_no_atoms():
    residues = _buried_polar_vs_exposed_nonpolar()
    # "A:404" is requested but absent from the structure -> silently skipped.
    scored = structural_essential_scores(residues, {"A:1", "A:404"})
    assert {rid for rid, _ in scored} == {"A:1"}


def test_p2rank_breaks_a_structural_tie():
    """When burial and polarity are tied, ligandability decides the order."""
    residues = {
        "A:1": [ProteinAtom(0.0, 0.0, 0.0, "OA")],
        "A:2": [ProteinAtom(20.0, 0.0, 0.0, "OA")],
        # Equal burial: one neighbour each, symmetric.
        "A:98": [ProteinAtom(1.0, 0.0, 0.0, "C")],
        "A:99": [ProteinAtom(21.0, 0.0, 0.0, "C")],
    }
    tied = [rid for rid, s in structural_essential_scores(residues, {"A:1", "A:2"})]
    assert set(tied) == {"A:1", "A:2"}
    broken = [
        rid
        for rid, _ in structural_essential_scores(
            residues, {"A:1", "A:2"}, p2rank_scores={"A:2": 100.0, "A:1": 0.0}
        )
    ]
    assert broken == ["A:2", "A:1"]


# --- Route C + gating: EssentialResidues ---------------------------------


def test_static_set_gates_immediately():
    er = EssentialResidues(static={"A:25"})
    assert er.is_gating()
    assert er.engaged_count({"A:25", "A:50"}) == 1
    assert er.engaged_count({"A:50"}) == 0


def test_empty_set_does_not_gate():
    """The garbage guard: no static, not enough emergent -> tier stays off."""
    er = EssentialResidues(static=set(), emergent_min_poses=3)
    assert not er.is_gating()
    er.observe_calibration({"A:25"}, cnn_pose_score=0.9)
    er.observe_calibration({"A:25"}, cnn_pose_score=0.9)
    assert not er.is_gating()  # still below emergent_min_poses


def test_emergent_residue_joins_after_confident_convergence():
    er = EssentialResidues(
        static=set(), emergent_fraction=0.6, emergent_min_poses=3, emergent_min_pose_score=0.6
    )
    er.observe_calibration({"A:25", "A:30"}, cnn_pose_score=0.9)
    er.observe_calibration({"A:25", "A:41"}, cnn_pose_score=0.8)
    er.observe_calibration({"A:25"}, cnn_pose_score=0.7)
    # A:25 hit by all 3 (>= 0.6*3); A:30/A:41 by one each.
    assert er.emergent() == {"A:25"}
    assert er.is_gating()
    assert er.engaged_count({"A:25"}) == 1


def test_low_confidence_poses_do_not_vote():
    er = EssentialResidues(static=set(), emergent_min_poses=1, emergent_min_pose_score=0.6)
    er.observe_calibration({"A:25"}, cnn_pose_score=0.3)
    er.observe_calibration({"A:25"}, cnn_pose_score=None)
    assert er.emergent() == set()
    assert not er.is_gating()


def test_active_set_capped_and_static_kept():
    er = EssentialResidues(
        static={"A:1", "A:2", "A:3"},
        emergent_fraction=0.5,
        emergent_min_poses=1,
        emergent_min_pose_score=0.0,
        max_active=4,
    )
    # Three emergent residues, differing support; only one slot remains.
    for _ in range(3):
        er.observe_calibration({"A:9"}, cnn_pose_score=0.9)
    er.observe_calibration({"A:8"}, cnn_pose_score=0.9)
    active = er.active()
    assert {"A:1", "A:2", "A:3"} <= active
    assert len(active) == 4
    assert "A:9" in active and "A:8" not in active  # best-supported emergent wins the slot


def test_state_round_trips_for_resume():
    er = EssentialResidues(static={"A:1"}, emergent_min_poses=2)
    er.observe_calibration({"A:1", "A:5"}, cnn_pose_score=0.9)
    er.observe_calibration({"A:5"}, cnn_pose_score=0.9)

    restored = EssentialResidues(static={"A:1"}, emergent_min_poses=2)
    restored.load_state(er.state_dict())
    assert restored.emergent() == er.emergent()
    assert restored.active() == er.active()


# --- Integration: real structure, treated as unknown ---------------------


def test_recovers_catalytic_aspartate_of_hiv_protease():
    """Treat hiv_protease as an unknown protein: the structure-only ranking
    should surface its catalytic aspartate dyad (Asp25/Asp25') without any
    inhibitor or knowledge of what the protein is."""
    receptor = "targets/hiv_protease/protein.pdbqt"
    if not os.path.exists(receptor):
        return  # fixture not built in this checkout; nothing to assert
    config_path = "targets/hiv_protease/config.yaml"
    import yaml

    pocket_ids = set(yaml.safe_load(open(config_path))["pocket"]["residue_ids"])
    all_residues = parse_receptor_residues(receptor)
    top = select_static_essential(all_residues, pocket_ids, count=4)
    assert "A:25" in top or "B:25" in top
