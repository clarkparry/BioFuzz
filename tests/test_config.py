from __future__ import annotations

from pathlib import Path

from biofuzz.docking.config import (
    DEFAULT_GLOBAL_CONFIG,
    BoxConfig,
    OracleConfig,
    PocketConfig,
    TargetConfig,
    apply_global_defaults_to_target,
    load_global_config,
    load_target_config,
)


def test_load_global_config_returns_deep_copy_for_missing_file(tmp_path: Path) -> None:
    cfg = load_global_config(tmp_path / "missing_config.yaml")
    cfg["docking"]["engine"] = "vina"

    assert DEFAULT_GLOBAL_CONFIG["docking"]["engine"] == "gnina"


def test_load_target_config_resolves_paths_relative_to_target_dir(tmp_path: Path) -> None:
    targets_dir = tmp_path / "targets"
    target_dir = targets_dir / "mini"
    target_dir.mkdir(parents=True)

    (target_dir / "config.py").write_text(
        (
            "from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig\n"
            "TARGET = TargetConfig(\n"
            "    name='mini',\n"
            "    receptor='protein.pdbqt',\n"
            "    box=BoxConfig(center_x=0, center_y=0, center_z=0, size_x=10, size_y=10, size_z=10),\n"
            "    pocket=PocketConfig(residue_ids={1, 2, 3}, contact_cutoff=3.5),\n"
            ")\n"
        ),
        encoding="utf-8",
    )

    cfg = load_target_config("mini", targets_dir=targets_dir)

    assert cfg.receptor == str(target_dir / "protein.pdbqt")


def test_load_target_config_ignores_same_named_file_in_current_workdir(tmp_path: Path, monkeypatch) -> None:
    targets_dir = tmp_path / "targets"
    target_dir = targets_dir / "mini"
    target_dir.mkdir(parents=True)
    other_dir = tmp_path / "cwd"
    other_dir.mkdir()

    (other_dir / "protein.pdbqt").write_text("wrong receptor", encoding="utf-8")
    (target_dir / "protein.pdbqt").write_text("correct receptor", encoding="utf-8")
    (target_dir / "config.py").write_text(
        (
            "from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig\n"
            "TARGET = TargetConfig(\n"
            "    name='mini',\n"
            "    receptor='protein.pdbqt',\n"
            "    box=BoxConfig(center_x=0, center_y=0, center_z=0, size_x=10, size_y=10, size_z=10),\n"
            "    pocket=PocketConfig(residue_ids={1, 2, 3}, contact_cutoff=3.5),\n"
            ")\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(other_dir)
    cfg = load_target_config("mini", targets_dir=targets_dir)

    assert cfg.receptor == str(target_dir / "protein.pdbqt")


def test_apply_global_defaults_to_target_merges_oracle_defaults() -> None:
    target = TargetConfig(
        name="mini",
        receptor="protein.pdbqt",
        box=BoxConfig(center_x=0, center_y=0, center_z=0, size_x=10, size_y=10, size_z=10),
        pocket=PocketConfig(residue_ids={1, 2, 3}, contact_cutoff=3.5),
    )

    merged = apply_global_defaults_to_target(
        target,
        {
            "oracle": {
                "affinity_threshold": -8.5,
                "strain_threshold": 2.5,
                "selectivity_ratio_min": 3.0,
            }
        },
    )

    assert merged.oracle.affinity_threshold == -8.5
    assert merged.oracle.strain_threshold == 2.5
    assert merged.oracle.selectivity_ratio_min == 3.0


def test_apply_global_defaults_to_target_preserves_target_specific_oracle_values() -> None:
    target = TargetConfig(
        name="mini",
        receptor="protein.pdbqt",
        box=BoxConfig(center_x=0, center_y=0, center_z=0, size_x=10, size_y=10, size_z=10),
        pocket=PocketConfig(residue_ids={1, 2, 3}, contact_cutoff=3.5),
        oracle=OracleConfig(affinity_threshold=-10.5, strain_threshold=2.0),
    )

    merged = apply_global_defaults_to_target(
        target,
        {
            "oracle": {
                "affinity_threshold": -8.5,
                "strain_threshold": 4.0,
                "selectivity_ratio_min": 3.0,
            }
        },
    )

    assert merged.oracle.affinity_threshold == -10.5
    assert merged.oracle.strain_threshold == 2.0
    assert merged.oracle.selectivity_ratio_min == 3.0


def test_apply_global_defaults_preserves_explicit_default_oracle_values() -> None:
    target = TargetConfig(
        name="mini",
        receptor="protein.pdbqt",
        box=BoxConfig(center_x=0, center_y=0, center_z=0, size_x=10, size_y=10, size_z=10),
        pocket=PocketConfig(residue_ids={1, 2, 3}, contact_cutoff=3.5),
        oracle=OracleConfig(affinity_threshold=-9.0),
    )

    merged = apply_global_defaults_to_target(
        target,
        {
            "oracle": {
                "affinity_threshold": -8.5,
                "strain_threshold": 2.5,
                "selectivity_ratio_min": 3.0,
            }
        },
    )

    assert merged.oracle.affinity_threshold == -9.0
    assert merged.oracle.strain_threshold == 2.5
    assert merged.oracle.selectivity_ratio_min == 3.0


def test_bundled_targets_load_with_prepared_assets() -> None:
    expected_targets = {
        "hiv_protease": ("indinavir.smi", "indinavir.pdbqt"),
        "egfr_kinase": ("erlotinib.smi", "erlotinib.pdbqt"),
        "parp1": ("talazoparib.smi", "talazoparib.pdbqt"),
        "sars_cov2_mpro": ("nirmatrelvir.smi", "nirmatrelvir.pdbqt"),
        "braf_v600e": ("vemurafenib.smi", "vemurafenib.pdbqt"),
    }

    for target_name, reference_files in expected_targets.items():
        cfg = load_target_config(target_name)
        target_dir = Path("targets") / target_name

        assert cfg.name == target_name
        assert Path(cfg.receptor).exists()
        assert target_dir.joinpath("config.py").exists()
        for filename in reference_files:
            assert target_dir.joinpath("reference_ligands", filename).exists()
