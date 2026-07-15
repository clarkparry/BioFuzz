from biofuzz.protein import parse_receptor_residues, residue_key

HIV_RECEPTOR = "targets/hiv_protease/protein.pdbqt"


def test_residue_key_format():
    assert residue_key("A", "42") == "A:42"


def test_parse_receptor_residues_real_fixture():
    residues = parse_receptor_residues(HIV_RECEPTOR, residue_ids={"A:25", "A:8", "B:25"})
    assert set(residues.keys()) == {"A:25", "A:8", "B:25"}
    for atoms in residues.values():
        assert len(atoms) > 0
        for atom in atoms:
            assert not atom.type.upper().startswith("H")


def test_parse_receptor_residues_no_filter_returns_all():
    residues = parse_receptor_residues(HIV_RECEPTOR)
    assert len(residues) > 90  # HIV protease dimer, ~99 residues per chain
    assert any(rid.startswith("A:") for rid in residues)
    assert any(rid.startswith("B:") for rid in residues)
