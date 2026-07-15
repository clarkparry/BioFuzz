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
