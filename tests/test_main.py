from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

import pytest

import main as main_module
from biofuzz.docking import runner as docking_runner
from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig
from biofuzz.molecules import preparation


REPO_ROOT = Path(__file__).resolve().parents[1]


def _live_runtime_available() -> bool:
    return (
        preparation.Chem is not None
        and preparation.AllChem is not None
        and preparation.meeko_available()
        and docking_runner._resolve_binary("vina") is not None
    )


def _parse_cli_stats(stdout: str) -> dict[str, str]:
    stats: dict[str, str] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith(("iterations:", "hits:", "coverage_ratio:", "corpus_size:", "best_affinity:", "total_docks:", "stopped_reason:")):
            continue
        key, value = line.split(":", 1)
        stats[key.strip()] = value.strip()
    return stats


def test_missing_runtime_dependencies_reports_expected_components(monkeypatch) -> None:
    monkeypatch.setattr(main_module.preparation, "Chem", None)
    monkeypatch.setattr(main_module.preparation, "AllChem", None)
    monkeypatch.setattr(main_module.preparation, "meeko_available", lambda: False)
    monkeypatch.setattr(main_module.docking_runner.shutil, "which", lambda _engine: None)
    monkeypatch.setattr(
        main_module.docking_runner,
        "REPO_TOOL_BIN_DIR",
        Path("/definitely/missing/biofuzz-tool-bin"),
    )

    missing = main_module._missing_runtime_dependencies("gnina")

    assert missing == ["RDKit", "Meeko", "docking binary (gnina)"]


def test_missing_runtime_dependencies_accepts_supported_binary_fallback(monkeypatch) -> None:
    monkeypatch.setattr(main_module.preparation, "Chem", object())
    monkeypatch.setattr(main_module.preparation, "AllChem", object())
    monkeypatch.setattr(main_module.preparation, "meeko_available", lambda: True)
    monkeypatch.setattr(
        main_module.docking_runner,
        "REPO_TOOL_BIN_DIR",
        Path("/definitely/missing/biofuzz-tool-bin"),
    )

    def fake_which(name: str) -> str | None:
        if name == "vina":
            return "/usr/bin/vina"
        return None

    monkeypatch.setattr(main_module.docking_runner.shutil, "which", fake_which)

    missing = main_module._missing_runtime_dependencies("gnina")

    assert missing == []


def test_main_allows_zero_iteration_smoke_run_without_runtime_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("RECEPTOR\n", encoding="utf-8")
    seeds = tmp_path / "seeds.smi"
    seeds.write_text("CCO seed_1\n", encoding="utf-8")

    args = argparse.Namespace(
        target="mini",
        seeds=str(seeds),
        output=str(tmp_path / "run"),
        max_iterations=0,
        workers=1,
        checkpoint_every=10,
        mutations_per_entry=1,
        engine=None,
        config=str(tmp_path / "config.yaml"),
    )
    global_cfg = {
        "docking": {
            "exhaustiveness_fuzz": 4,
            "exhaustiveness_confirm": 16,
            "num_modes": 3,
            "engine": "gnina",
        },
        "oracle": {
            "affinity_threshold": -9.0,
            "strain_threshold": 3.5,
            "selectivity_ratio_min": 2.0,
        },
        "molecules": {
            "max_mw": 550,
            "max_logp": 5.0,
            "max_hbd": 5,
            "max_hba": 10,
            "max_rot_bonds": 10,
            "mutations_per_entry": 20,
        },
        "corpus": {
            "max_size": 50000,
            "priority_new_bit_weight": 10.0,
            "priority_affinity_weight": 1.0,
            "priority_reuse_penalty": 0.1,
        },
        "fuzzer": {
            "workers": 4,
            "checkpoint_every": 500,
            "log_level": "INFO",
        },
    }
    target_cfg = TargetConfig(
        name="mini",
        receptor=str(receptor),
        box=BoxConfig(center_x=0.0, center_y=0.0, center_z=0.0, size_x=10.0, size_y=10.0, size_z=10.0),
        pocket=PocketConfig(residue_ids={1, 2}, contact_cutoff=3.5),
    )

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module, "load_global_config", lambda path: global_cfg)
    monkeypatch.setattr(main_module, "load_target_config", lambda target: target_cfg)
    monkeypatch.setattr(
        main_module,
        "apply_global_defaults_to_target",
        lambda config, _global_cfg: config,
    )
    monkeypatch.setattr(main_module, "_missing_runtime_dependencies", lambda engine: ["RDKit"])

    run_calls: list[dict[str, object]] = []

    def fake_run(**kwargs):
        run_calls.append(kwargs)
        return {
            "iterations": 0,
            "hits": 0,
            "coverage_ratio": 0.0,
            "corpus_size": 1,
            "best_affinity": 0.0,
        }

    monkeypatch.setattr(main_module, "run", fake_run)

    assert main_module.main() == 0
    assert len(run_calls) == 1


def test_main_returns_interrupt_exit_code_when_run_stops_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("RECEPTOR\n", encoding="utf-8")
    seeds = tmp_path / "seeds.smi"
    seeds.write_text("CCO seed_1\n", encoding="utf-8")

    args = argparse.Namespace(
        target="mini",
        seeds=str(seeds),
        output=str(tmp_path / "run"),
        max_iterations=1,
        workers=1,
        checkpoint_every=10,
        mutations_per_entry=1,
        engine="vina",
        config=str(tmp_path / "config.yaml"),
    )
    global_cfg = {
        "docking": {
            "exhaustiveness_fuzz": 4,
            "exhaustiveness_confirm": 16,
            "num_modes": 3,
            "engine": "vina",
        },
        "oracle": {
            "affinity_threshold": -9.0,
            "strain_threshold": 3.5,
            "selectivity_ratio_min": 2.0,
        },
        "molecules": {
            "max_mw": 550,
            "max_logp": 5.0,
            "max_hbd": 5,
            "max_hba": 10,
            "max_rot_bonds": 10,
            "mutations_per_entry": 20,
        },
        "corpus": {
            "max_size": 50000,
            "priority_new_bit_weight": 10.0,
            "priority_affinity_weight": 1.0,
            "priority_reuse_penalty": 0.1,
        },
        "fuzzer": {
            "workers": 4,
            "checkpoint_every": 500,
            "log_level": "INFO",
        },
    }
    target_cfg = TargetConfig(
        name="mini",
        receptor=str(receptor),
        box=BoxConfig(center_x=0.0, center_y=0.0, center_z=0.0, size_x=10.0, size_y=10.0, size_z=10.0),
        pocket=PocketConfig(residue_ids={1, 2}, contact_cutoff=3.5),
    )

    monkeypatch.setattr(main_module, "parse_args", lambda: args)
    monkeypatch.setattr(main_module, "load_global_config", lambda path: global_cfg)
    monkeypatch.setattr(main_module, "load_target_config", lambda target: target_cfg)
    monkeypatch.setattr(
        main_module,
        "apply_global_defaults_to_target",
        lambda config, _global_cfg: config,
    )
    monkeypatch.setattr(main_module, "_missing_runtime_dependencies", lambda engine: [])
    monkeypatch.setattr(
        main_module,
        "run",
        lambda **kwargs: {
            "iterations": 12,
            "hits": 1,
            "coverage_ratio": 0.5,
            "corpus_size": 42,
            "best_affinity": -10.1,
            "total_docks": 17,
            "stopped_reason": "keyboard_interrupt",
        },
    )

    assert main_module.main() == 130


@pytest.mark.skipif(
    not _live_runtime_available(),
    reason="live docking regression requires RDKit, Meeko, and a resolved vina binary",
)
def test_main_live_cli_hiv_target_smoke(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.smi"
    seed_path.write_text("c1ccc(O)cc1 phenol\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in [str(REPO_ROOT), env.get("PYTHONPATH", "")] if value
    )

    proc = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--target",
            "hiv_protease",
            "--engine",
            "vina",
            "--seeds",
            str(seed_path),
            "--mutations-per-entry",
            "1",
            "--max-iterations",
            "1",
            "--workers",
            "1",
            "--output",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    assert "Run complete" in proc.stdout

    stats = _parse_cli_stats(proc.stdout)

    assert stats["iterations"] == "1"
    assert stats["corpus_size"] == "2"
    assert stats["total_docks"] == "1"
    assert stats["stopped_reason"] == "max_iterations"
    assert float(stats["best_affinity"]) < 0.0

    assert (output_dir / "coverage.json").exists()
    assert (output_dir / "corpus" / "state.json").exists()
    assert (output_dir / "corpus.json").exists()
