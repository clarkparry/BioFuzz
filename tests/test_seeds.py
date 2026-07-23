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


def test_no_per_target_inhibitor_seeds_are_bundled():
    """A target's known inhibitor must not be injected into the corpus.

    Discovery may not be handed its own answer. There is no seeds/per_target/
    tree any more; the only seed source is the target-agnostic approved-drug
    set. If a known binder is to be a seed it has to earn a place in
    seeds/approved_drugs.smi and then competes at the ordinary drug-likeness
    prior, with no special weight.
    """
    import os

    assert not os.path.exists("seeds/per_target")


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


def test_seed_priority_takes_no_target_specific_flag():
    """The target_specific priority boost is gone.

    It used to add a flat +5.0 so a target's own inhibitor floated to the top
    of the queue and was docked first. compute_seed_priority no longer accepts
    the flag at all, so no seed can be privileged for being the known answer.
    """
    import inspect

    params = inspect.signature(compute_seed_priority).parameters
    assert "target_specific" not in params


def test_scaffold_diversity_bonus_decreases_with_repetition():
    smiles = "CC(=O)Oc1ccccc1C(=O)O"
    mol = Chem.MolFromSmiles(smiles)
    from rdkit.Chem.Scaffolds import MurckoScaffold

    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)

    fresh = compute_seed_priority(smiles, corpus_scaffold_counts={})
    seen_once = compute_seed_priority(smiles, corpus_scaffold_counts={scaffold: 1})
    seen_many = compute_seed_priority(smiles, corpus_scaffold_counts={scaffold: 5})
    assert fresh >= seen_once >= seen_many
