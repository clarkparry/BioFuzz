import math

import pytest

from biofuzz.docker.parser import DockingMode, parse_log
from biofuzz.oracle.scoring import (
    KCAL_PER_LOG_UNIT,
    best_mode,
    ligand_efficiency,
    pkd_to_kcal,
    score_mode,
)

# Verbatim gnina 1.3.2 CNN-scoring output.
GNINA_CNN_LOG = """
mode |  affinity  |  intramol  |    CNN     |   CNN
     | (kcal/mol) | (kcal/mol) | pose score | affinity
-----+------------+------------+------------+----------
    1       -9.81        1.30       0.3207      6.795
    2       -8.92        0.32       0.2810      6.600
    3       -9.07       -0.75       0.2466      6.376
"""

# gnina with --cnn_scoring=none degrades to vina's 4-column table.
VINA_LOG = """
mode |   affinity | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -9.810      0.000      0.000
   2       -8.920      1.234      2.345
"""


def test_parses_all_five_gnina_columns():
    modes = parse_log(GNINA_CNN_LOG)
    assert len(modes) == 3
    first = modes[0]
    assert first.affinity == -9.81
    # The columns that used to be silently dropped.
    assert first.intramol == 1.30
    assert first.cnn_pose_score == 0.3207
    assert first.cnn_affinity == 6.795


def test_parses_vina_four_column_table():
    modes = parse_log(VINA_LOG)
    assert len(modes) == 2
    assert modes[0].affinity == -9.81
    assert modes[0].rmsd_lb == 0.0
    assert modes[0].rmsd_ub == 0.0
    # No CNN columns in this layout.
    assert modes[0].cnn_affinity is None
    assert modes[0].intramol is None


def test_pkd_to_kcal_conversion():
    assert pkd_to_kcal(0.0) == 0.0
    assert pkd_to_kcal(6.795) == pytest.approx(-9.268, abs=1e-3)
    # Tighter binding (higher pKd) must map to more negative kcal/mol.
    assert pkd_to_kcal(9.0) < pkd_to_kcal(6.0)
    assert pkd_to_kcal(1.0) == pytest.approx(-KCAL_PER_LOG_UNIT)


def test_scoring_policies_disagree_as_expected():
    mode = DockingMode(mode=1, affinity=-9.81, intramol=1.3, cnn_pose_score=0.32, cnn_affinity=6.795)

    assert score_mode(mode, "vina").score == -9.81
    assert score_mode(mode, "cnn").score == pytest.approx(-9.268, abs=1e-3)
    # Consensus takes the weaker (least negative) of the two.
    assert score_mode(mode, "consensus").score == pytest.approx(-9.268, abs=1e-3)


def test_consensus_takes_the_weaker_estimate():
    # vina optimistic, CNN pessimistic -> consensus follows the CNN.
    optimistic_vina = DockingMode(mode=1, affinity=-12.0, cnn_affinity=5.0)
    assert score_mode(optimistic_vina, "consensus").score == pytest.approx(pkd_to_kcal(5.0))

    # CNN optimistic, vina pessimistic -> consensus follows vina.
    optimistic_cnn = DockingMode(mode=1, affinity=-6.0, cnn_affinity=10.0)
    assert score_mode(optimistic_cnn, "consensus").score == -6.0


def test_scoring_falls_back_to_vina_without_cnn_columns():
    mode = DockingMode(mode=1, affinity=-9.81, rmsd_lb=0.0, rmsd_ub=0.0)
    for policy in ("vina", "cnn", "consensus"):
        assert score_mode(mode, policy).score == -9.81


def test_unknown_policy_rejected():
    with pytest.raises(ValueError):
        score_mode(DockingMode(mode=1, affinity=-9.0), "magic")


def test_disagreement_measures_gap_between_functions():
    mode = DockingMode(mode=1, affinity=-6.0, cnn_affinity=10.0)
    scored = score_mode(mode, "consensus")
    assert scored.disagreement == pytest.approx(abs(-6.0 - pkd_to_kcal(10.0)))

    assert score_mode(DockingMode(mode=1, affinity=-6.0), "vina").disagreement is None


def test_best_mode_reranks_under_policy():
    # gnina ranks by CNN here: mode 1 has the best CNN but not the best vina.
    modes = parse_log(GNINA_CNN_LOG)

    best_vina, scored_vina = best_mode(modes, "vina")
    assert best_vina.mode == 1  # -9.81 is the most negative vina score
    assert scored_vina.score == -9.81

    best_cnn, scored_cnn = best_mode(modes, "cnn")
    assert best_cnn.mode == 1  # 6.795 is the highest pKd
    assert scored_cnn.score == pytest.approx(pkd_to_kcal(6.795))


def test_best_mode_empty():
    assert best_mode([], "consensus") == (None, None)


def test_ligand_efficiency():
    assert ligand_efficiency(-10.0, 40) == pytest.approx(0.25)
    # Same score on a smaller molecule is a better lead.
    assert ligand_efficiency(-10.0, 20) > ligand_efficiency(-10.0, 40)
    assert ligand_efficiency(-10.0, 0) is None


def test_ligand_efficiency_exposes_size_bias():
    """A big molecule can beat a small one on raw score yet lose on LE.

    This is the bias that made every reference-campaign hit an MW-500+
    Lipinski violator.
    """
    big_but_inefficient = ligand_efficiency(-11.0, 45)
    small_and_efficient = ligand_efficiency(-8.0, 20)
    assert -11.0 < -8.0  # the big one wins on raw affinity
    assert small_and_efficient > big_but_inefficient  # ... and loses on LE
    assert not math.isclose(small_and_efficient, big_but_inefficient)
