from biofuzz.corpus import CorpusEntry
from biofuzz.fuzzer.config import load_global_config, load_target_config, merge_defaults
from biofuzz.fuzzer.status import RuntimeStatus


def test_load_global_config():
    config = load_global_config("config.yaml")
    assert "docking" in config
    assert config["docking"]["engine"] == "gnina"


def test_target_config_has_no_reference_derived_oracle_threshold():
    config = load_target_config("hiv_protease")
    assert config["name"] == "hiv_protease"
    # No target ships its own affinity_threshold any more: thresholds used to be
    # calibrated from where the known inhibitor docks, and that reliance is gone.
    # A target inherits the reference-free global oracle instead.
    assert "affinity_threshold" not in config.get("oracle", {})
    assert len(config["pocket"]["residue_ids"]) > 0


def test_merge_defaults_gives_targets_the_global_reference_free_oracle():
    global_config = load_global_config("config.yaml")
    target_config = load_target_config("egfr_kinase")
    merged = merge_defaults(global_config, target_config)
    # With no per-target oracle override, the target inherits the global oracle
    # wholesale -- the same reference-free threshold and tiers for every target.
    assert (
        merged["oracle"]["affinity_threshold"]
        == global_config["oracle"]["affinity_threshold"]
    )
    assert merged["oracle"]["strain_threshold"] == 3.5  # inherited from global
    assert merged["docking"]["engine"] == "gnina"
    # Global-only oracle tiers still reach the target.
    assert "scoring_policy" in merged["oracle"]
    assert "min_ligand_efficiency" in merged["oracle"]


def test_merge_defaults_preserves_per_target_docking_override():
    """A target's own docking settings must not be discarded.

    merge_defaults used to assign `docking` straight from the global config,
    silently dropping any per-target override.
    """
    global_config = {"docking": {"engine": "gnina", "exhaustiveness_fuzz": 8}}
    target_config = {"docking": {"exhaustiveness_fuzz": 32}}
    merged = merge_defaults(global_config, target_config)
    assert merged["docking"]["exhaustiveness_fuzz"] == 32  # target wins
    assert merged["docking"]["engine"] == "gnina"  # global fills the gap


def test_runtime_status_constructible_with_minimal_fields():
    status = RuntimeStatus(stage="dock", mutation_stage="havoc")
    assert status.stage == "dock"
    assert status.hits == 0
    assert status.best_affinity is None


def test_campaign_stage_selection_first_time_is_deterministic(tmp_path):
    from biofuzz.fuzzer.campaign import Campaign

    global_config = load_global_config("config.yaml")
    target_config = merge_defaults(global_config, load_target_config("hiv_protease"))

    campaign = Campaign(
        target_name="hiv_protease",
        target_config=target_config,
        global_config=global_config,
        output_dir=tmp_path,
        workers=1,
    )
    entry = CorpusEntry(smiles="c1ccccc1")
    campaign.state.corpus.add(entry)

    stage, donor = campaign._select_stage(entry)
    assert stage == "deterministic"
    assert donor is None
    campaign.log.close()


def test_campaign_stage_selection_after_first_pass_is_splice_or_havoc(tmp_path):
    from biofuzz.fuzzer.campaign import Campaign

    global_config = load_global_config("config.yaml")
    target_config = merge_defaults(global_config, load_target_config("hiv_protease"))

    campaign = Campaign(
        target_name="hiv_protease",
        target_config=target_config,
        global_config=global_config,
        output_dir=tmp_path,
        workers=1,
    )
    entry = CorpusEntry(smiles="c1ccccc1", times_selected=2)
    donor_entry = CorpusEntry(smiles="CCO")
    campaign.state.corpus.add(entry)
    campaign.state.corpus.add(donor_entry)

    stage, donor = campaign._select_stage(entry)
    assert stage in ("splice", "havoc")
    campaign.log.close()


def test_campaign_ctrl_c_checkpoints_and_reraises(tmp_path):
    # Real SIGINT delivery isn't reliably testable from this automated
    # harness (backgrounded processes have SIGINT masked to SIG_IGN so they
    # survive unrelated interrupts -- confirmed via /proc/<pid>/status
    # SigIgn during manual investigation). This exercises the actual
    # Ctrl-C handling logic in-process instead: KeyboardInterrupt raised
    # mid-iteration must still produce a checkpoint and propagate.
    from biofuzz.fuzzer.campaign import Campaign

    global_config = load_global_config("config.yaml")
    target_config = merge_defaults(global_config, load_target_config("hiv_protease"))

    campaign = Campaign(
        target_name="hiv_protease",
        target_config=target_config,
        global_config=global_config,
        output_dir=tmp_path,
        workers=1,
    )
    campaign.state.corpus.add(CorpusEntry(smiles="c1ccccc1", priority=1.0))

    def _raise_interrupt():
        raise KeyboardInterrupt()

    campaign.run_iteration = _raise_interrupt

    try:
        campaign.run()
        assert False, "expected KeyboardInterrupt to propagate"
    except KeyboardInterrupt:
        pass

    assert campaign.state.stopped_reason == "keyboard_interrupt"
    assert (campaign.layout.corpus_dir / "state.json").exists()
    assert campaign.layout.coverage_path.exists()
    assert campaign.log._fh.closed
