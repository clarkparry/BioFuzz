from biofuzz.docking.parser import parse_log, parse_pose


def test_parse_log_extracts_modes() -> None:
    log = """
Random header
-----+------------+----------+----------
   1       -10.8      0.000      0.000
   2        -9.4      1.205      2.044
   3        -8.7      2.100      3.200
"""
    modes = parse_log(log)
    assert len(modes) == 3
    assert modes[0].mode == 1
    assert modes[0].affinity == -10.8
    assert modes[2].rmsd_ub == 3.2


def test_parse_pose_reads_first_model_only() -> None:
    pdbqt = """
MODEL 1
ATOM      1  C1  LIG A   1       1.000   2.000   3.000  0.00  0.00  -0.123 C
ATOM      2  O2  LIG A   1       2.000   3.000   4.000  0.00  0.00  -0.456 OA
ENDMDL
MODEL 2
ATOM      3  N3  LIG A   1       9.000   9.000   9.000  0.00  0.00  -0.100 N
ENDMDL
"""
    atoms = parse_pose(pdbqt)
    assert len(atoms) == 2
    assert atoms[0].name == "C1"
    assert atoms[1].type == "OA"


def test_parse_pose_handles_condensed_spacing() -> None:
    pdbqt = """
ATOM 1 C1 LIG A 1 1.0 2.0 3.0 0.00 0.00 -0.123 C
ATOM 2 O2 LIG A 1 2.0 3.0 4.0 0.00 0.00 -0.456 OA
"""
    atoms = parse_pose(pdbqt)
    assert len(atoms) == 2
    assert atoms[0].x == 1.0
    assert atoms[0].y == 2.0
    assert atoms[0].z == 3.0
    assert atoms[0].charge == -0.123
    assert atoms[1].type == "OA"
