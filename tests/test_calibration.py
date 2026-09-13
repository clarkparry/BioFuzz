"""Seed calibration: the corpus entry itself gets docked, not only its mutants."""

from biofuzz.corpus import Corpus, CorpusEntry
from biofuzz.seeds.priority import base_affinity_estimate, compute_seed_priority


def test_seed_priority_survives_entering_the_corpus():
    """A seed's prior must not be flattened by the queue.

    Seed priors are computed by compute_seed_priority and passed in on the
    CorpusEntry. They are intrinsic worth, so they belong in base_priority --
    if the queue recomputed priority from an uncalibrated entry's (empty)
    evidence it would floor every seed at 0.1 and the seed ordering would be
    lost entirely.
    """
    corpus = Corpus()
    smiles = "CC(=O)Oc1ccccc1C(=O)O"
    prior = compute_seed_priority(smiles)
    assert prior > 0.1

    entry = corpus.add(CorpusEntry(smiles=smiles, source_id="approved_x", priority=prior))
    assert entry.base_priority == prior
    assert corpus.pop().base_priority == prior


def test_mw_prior_is_a_band_not_a_ramp():
    """The MW term must not reward sheer heaviness.

    An unbounded ramp in MW would make the heaviest seed in the file the first
    one popped, compounding docking's own size bias. A molecule near the centre
    of the drug-like band must beat a much heavier one.
    """
    aspirin = base_affinity_estimate("CC(=O)Oc1ccccc1C(=O)O")  # MW 180
    sildenafil = base_affinity_estimate(
        "CCCc1nn(C)c2c(=O)[nH]c(-c3cc(S(=O)(=O)N4CCN(C)CC4)ccc3OCC)nc12"
    )  # MW 475
    # Something near the 350 Da centre of the drug-like band.
    midband = base_affinity_estimate("O=C(Nc1ccc(Cl)cc1)c1ccc(S(=O)(=O)N)cc1")  # ~331

    assert midband > sildenafil
    assert midband > aspirin
    assert base_affinity_estimate("invalid$$smiles") == 0.0


def test_uncalibrated_entry_is_flagged():
    entry = CorpusEntry(smiles="CCO")
    assert not entry.calibrated
    assert entry.best_affinity is None


def test_calibration_marks_entry_and_records_affinity():
    """After the entry itself is docked it carries real evidence."""
    corpus = Corpus(novelty_weight=10.0)
    entry = corpus.add("CCO", novelty=2, affinity=-9.5)
    entry.calibrated = True

    assert entry.best_affinity == -9.5
    # An entry with evidence must outrank a bare, never-docked one.
    bare = corpus.add(CorpusEntry(smiles="CCC"))
    assert corpus.compute_priority(entry) > corpus.compute_priority(bare)


# --- Per-target molecule bounds ---


def test_every_target_can_prepare_its_own_reference_drug():
    """A target whose bounds reject its own known drug can never find one.

    Preparation drops molecules outside the `molecules:` envelope before docking,
    with no finding and, for a mutant, no log line. The global max_mw of 550
    excludes indinavir (614 Da), so under global-only bounds no molecule of the
    peptidomimetic class that actually inhibits HIV protease could be generated
    at all. This asserts each target's own bounds admit its own chemistry.
    See docs/adding_targets.md.
    """
    from pathlib import Path

    from biofuzz.fuzzer import load_global_config, load_target_config, merge_defaults
    from biofuzz.prep import prepare_smiles

    global_config = load_global_config("config.yaml")
    targets = sorted(
        p.name for p in Path("targets").iterdir() if (p / "config.yaml").exists()
    )
    assert targets, "no targets found"

    rejected = []
    for target in targets:
        merged = merge_defaults(global_config, load_target_config(target))
        mol_cfg = merged["molecules"]
        filter_kwargs = dict(
            min_mw=mol_cfg.get("min_mw", 0.0),
            max_mw=mol_cfg["max_mw"],
            max_logp=mol_cfg["max_logp"],
            max_hbd=mol_cfg.get("max_hbd", 10),
            max_hba=mol_cfg.get("max_hba", 10),
            max_rot_bonds=mol_cfg["max_rot_bonds"],
        )
        for smi_file in Path(f"targets/{target}/reference_ligands").glob("*.smi"):
            smiles = smi_file.read_text().split()[0]
            if prepare_smiles(smiles, **filter_kwargs) is None:
                rejected.append(f"{target}/{smi_file.stem}")

    assert rejected == [], f"targets whose bounds reject their own reference drug: {rejected}"


def test_molecules_section_is_overridable_per_target():
    from biofuzz.fuzzer.config import merge_defaults

    global_config = {"molecules": {"max_mw": 550, "max_logp": 5.0}}
    target_config = {"molecules": {"max_mw": 750}}
    merged = merge_defaults(global_config, target_config)
    assert merged["molecules"]["max_mw"] == 750  # target wins
    assert merged["molecules"]["max_logp"] == 5.0  # global fills the gap
