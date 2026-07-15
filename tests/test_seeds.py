from rdkit import Chem

from biofuzz.seeds import compute_seed_priority, load_seeds


def test_approved_drugs_build_criterion():
    seeds = load_seeds("seeds/approved_drugs.smi")
    assert len(seeds) >= 50
    assert len(seeds) <= 500

    for smiles, seed_id in seeds:
        mol = Chem.MolFromSmiles(smiles)
        assert mol is not None, f"Invalid SMILES in seed: {seed_id}"
        assert smiles == Chem.MolToSmiles(mol, canonical=True), (
            f"Non-canonical SMILES in seed: {seed_id}"
        )

    smiles_set = {smiles for smiles, _ in seeds}
    assert len(smiles_set) == len(seeds), "Duplicate SMILES in seed file"


def test_per_target_seed_files_exist_and_are_valid():
    import os

    for target in ["hiv_protease", "egfr_kinase", "parp1", "sars_cov2_mpro", "braf_v600e"]:
        target_dir = f"seeds/per_target/{target}"
        assert os.path.isdir(target_dir)
        files = os.listdir(target_dir)
        assert len(files) >= 1
        for f in files:
            seeds = load_seeds(f"{target_dir}/{f}")
            assert len(seeds) >= 1
            for smiles, _ in seeds:
                assert Chem.MolFromSmiles(smiles) is not None


def test_load_seeds_parses_whitespace_format(tmp_path):
    seed_file = tmp_path / "seeds.smi"
    seed_file.write_text(
        "CC(=O)Oc1ccccc1C(=O)O aspirin\n"
        "# a comment line\n"
        "\n"
        "CCO ethanol\n"
    )
    seeds = load_seeds(seed_file)
    assert seeds == [("CC(=O)Oc1ccccc1C(=O)O", "aspirin"), ("CCO", "ethanol")]


def test_load_seeds_defaults_id_to_smiles_when_missing(tmp_path):
    seed_file = tmp_path / "seeds.smi"
    seed_file.write_text("CCO\n")
    seeds = load_seeds(seed_file)
    assert seeds == [("CCO", "CCO")]


def test_target_specific_seeds_get_priority_bonus():
    generic = compute_seed_priority("CC(=O)Oc1ccccc1C(=O)O", target_specific=False)
    specific = compute_seed_priority("CC(=O)Oc1ccccc1C(=O)O", target_specific=True)
    assert specific > generic


def test_scaffold_diversity_bonus_decreases_with_repetition():
    smiles = "CC(=O)Oc1ccccc1C(=O)O"
    mol = Chem.MolFromSmiles(smiles)
    from rdkit.Chem.Scaffolds import MurckoScaffold

    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)

    fresh = compute_seed_priority(smiles, corpus_scaffold_counts={})
    seen_once = compute_seed_priority(smiles, corpus_scaffold_counts={scaffold: 1})
    seen_many = compute_seed_priority(smiles, corpus_scaffold_counts={scaffold: 5})
    assert fresh >= seen_once >= seen_many
