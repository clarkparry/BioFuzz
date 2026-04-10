from __future__ import annotations

from pathlib import Path

import pytest

import biofuzz.core.fuzzer as fuzzer_module
from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.coverage import CoverageMap
from biofuzz.core.fuzzer import run
from biofuzz.docking.config import BoxConfig, OracleConfig, PocketConfig, TargetConfig
from biofuzz.docking.runner import DockingResult


def _corpus_checkpoint(output_dir: Path) -> Path:
    return output_dir / "corpus" / "state.json"


def _target_config(receptor: Path) -> TargetConfig:
    return TargetConfig(
        name="mini",
        receptor=str(receptor),
        box=BoxConfig(
            center_x=0.0,
            center_y=0.0,
            center_z=0.0,
            size_x=10.0,
            size_y=10.0,
            size_z=10.0,
        ),
        pocket=PocketConfig(residue_ids={1, 2}, contact_cutoff=3.5),
        oracle=OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5),
    )


def test_run_resumes_from_existing_checkpoints_without_reloading_seeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    cov = CoverageMap({1, 2})
    cov.update(frozenset({1}))
    cov.save(output_dir / "coverage.json")

    corpus = Corpus()
    corpus.add(CorpusEntry(smiles="CCO", source_id="resume", priority=5.0))
    corpus.save(output_dir / "corpus.json")

    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    def fail_load_smiles(_seed_path: str | Path) -> list[tuple[str, str]]:
        raise AssertionError("seed loading should not occur when corpus checkpoint exists")

    monkeypatch.setattr("biofuzz.core.fuzzer.load_smiles", fail_load_smiles)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
    )

    assert stats["iterations"] == 0
    assert stats["corpus_size"] == 1
    assert stats["coverage_ratio"] == pytest.approx(0.5)


def test_run_loads_seeds_when_checkpoint_missing(tmp_path: Path) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    seeds = tmp_path / "seeds.smi"
    seeds.write_text("CCO zinc_1\n", encoding="utf-8")

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=seeds,
        output_dir=tmp_path / "run",
        max_iterations=0,
        workers=1,
    )

    assert stats["iterations"] == 0
    assert stats["corpus_size"] == 1
    assert _corpus_checkpoint(tmp_path / "run").exists()
    assert (tmp_path / "run" / "corpus.json").exists()


def test_run_skips_mismatched_coverage_checkpoint(tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    CoverageMap({7, 8}).save(output_dir / "coverage.json")
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    seeds = tmp_path / "seeds.smi"
    seeds.write_text("CCO zinc_1\n", encoding="utf-8")
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=seeds,
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        logger=messages.append,
    )

    assert stats["coverage_ratio"] == 0.0
    assert any("skipping coverage checkpoint" in message.lower() for message in messages)


def test_run_reapplies_configured_corpus_max_size_after_resume(tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    corpus = Corpus()
    corpus.add(CorpusEntry(smiles="CCO", source_id="seed_1", priority=1.0))
    corpus.add(CorpusEntry(smiles="CCC", source_id="seed_2", priority=3.0))
    corpus.add(CorpusEntry(smiles="CCN", source_id="seed_3", priority=2.0))
    corpus.save(output_dir / "corpus.json")

    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "unused.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        max_corpus_size=1,
    )

    resumed = Corpus()
    resumed.load(_corpus_checkpoint(output_dir))

    assert stats["corpus_size"] == 1
    assert resumed.max_size == 1
    assert resumed.size() == 1


def test_run_prefers_new_corpus_checkpoint_layout_when_present(tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    legacy = Corpus()
    legacy.add(CorpusEntry(smiles="CCO", source_id="legacy", priority=1.0))
    legacy.save(output_dir / "corpus.json")

    primary = Corpus()
    primary.add(CorpusEntry(smiles="CCCC", source_id="primary", priority=8.0))
    primary.save(_corpus_checkpoint(output_dir))

    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "unused.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
    )

    resumed = Corpus()
    resumed.load(_corpus_checkpoint(output_dir))

    assert stats["corpus_size"] == 1
    assert resumed.pop().source_id == "primary"


def test_run_cleans_unprocessed_pose_files_when_max_iterations_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text(
        "ATOM      1  N   MET A   1       0.0   0.0   0.0\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(
        fuzzer_module,
        "load_smiles",
        lambda _path: [("CCO", "seed_1")],
    )
    monkeypatch.setattr(
        fuzzer_module,
        "mutate",
        lambda smiles, n=20, **kwargs: ["CCN", "CCC", "CCCl"],
    )
    monkeypatch.setattr(
        fuzzer_module,
        "prepare_smiles",
        lambda smiles, **kwargs: f"REMARK {smiles}\n",
    )

    pose_paths: list[Path] = []

    def fake_dock(*args, **kwargs) -> DockingResult:
        idx = len(pose_paths)
        pose_path = output_dir / f"pose_{idx}.pdbqt"
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        pose_paths.append(pose_path)
        return DockingResult(
            success=True,
            log_text="   1       -8.0      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    monkeypatch.setattr(fuzzer_module, "dock", fake_dock)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=1,
        mutations_per_entry=3,
    )

    assert stats["iterations"] == 1
    assert len(pose_paths) == 3
    assert all(not path.exists() for path in pose_paths)


def test_run_confirms_hits_before_saving_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text(
        "ATOM      1  N   MET A   1       0.0   0.0   0.0\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "mutate", lambda smiles, n=20, **kwargs: ["CCN"])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    docking_calls: list[int] = []

    def fake_dock(*args, **kwargs) -> DockingResult:
        docking_calls.append(kwargs["exhaustiveness"])
        pose_path = output_dir / f"pose_{len(docking_calls)}.pdbqt"
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1       0.5   0.0   0.0  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        affinity = -10.2 if len(docking_calls) == 1 else -10.5
        return DockingResult(
            success=True,
            log_text=f"   1       {affinity:.1f}      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    monkeypatch.setattr(fuzzer_module, "dock", fake_dock)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=1,
        mutations_per_entry=1,
        exhaustiveness=4,
        exhaustiveness_confirm=16,
    )

    findings_dirs = list((output_dir / "findings").iterdir())

    assert stats["hits"] == 1
    assert docking_calls == [4, 16]
    assert len(findings_dirs) == 1
    assert (findings_dirs[0] / "pose.pdbqt").exists()


def test_run_cycles_persistent_corpus_until_iteration_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text(
        "ATOM      1  N   MET A   1       0.0   0.0   0.0\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "mutate", lambda smiles, n=20, **kwargs: ["CCN"])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    def fake_dock(*args, **kwargs) -> DockingResult:
        pose_path = output_dir / f"pose_{kwargs.get('exhaustiveness', 0)}.pdbqt"
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        return DockingResult(
            success=True,
            log_text="   1       -8.0      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    monkeypatch.setattr(fuzzer_module, "dock", fake_dock)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=3,
        workers=1,
        mutations_per_entry=1,
    )

    assert stats["iterations"] == 3
    assert stats["corpus_size"] >= 1
    assert stats["total_docks"] >= 3


def test_run_counts_selectivity_docks_in_total_docks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text(
        "ATOM      1  N   MET A   1       0.0   0.0   0.0\n",
        encoding="utf-8",
    )
    offtarget = tmp_path / "offtarget.pdbqt"
    offtarget.write_text(
        "ATOM      1  N   MET A   1       0.0   0.0   0.0\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "mutate", lambda smiles, n=20, **kwargs: ["CCN"])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    dock_calls: list[str] = []

    def fake_dock(*args, **kwargs) -> DockingResult:
        target = args[1]
        dock_calls.append(getattr(target, "name", "direct"))
        pose_path = output_dir / f"pose_{len(dock_calls)}.pdbqt"
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        affinity = -10.0 if len(dock_calls) == 1 else -3.0
        return DockingResult(
            success=True,
            log_text=f"   1       {affinity:.1f}      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    monkeypatch.setattr(fuzzer_module, "dock", fake_dock)

    target = TargetConfig(
        name="mini",
        receptor=str(receptor),
        box=BoxConfig(center_x=0.0, center_y=0.0, center_z=0.0, size_x=10.0, size_y=10.0, size_z=10.0),
        pocket=PocketConfig(residue_ids={1, 2}, contact_cutoff=3.5),
        oracle=OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5, selectivity_ratio_min=2.0),
        offtarget_receptor=str(offtarget),
        offtarget_box=BoxConfig(center_x=0.0, center_y=0.0, center_z=0.0, size_x=10.0, size_y=10.0, size_z=10.0),
    )

    stats = run(
        target_config=target,
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=1,
        mutations_per_entry=1,
        exhaustiveness_confirm=0,
    )

    assert stats["iterations"] == 1
    assert stats["total_docks"] == 2
