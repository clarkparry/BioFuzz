from __future__ import annotations

from pathlib import Path
import time

import pytest

import biofuzz.core.fuzzer as fuzzer_module
from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.coverage import CoverageMap, CoverageObservation
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


def _make_successful_dock(
    output_dir: Path,
    *,
    affinity: float = -8.0,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
):
    calls = {"count": 0}

    def fake_dock(*args, **kwargs) -> DockingResult:
        calls["count"] += 1
        pose_path = output_dir / f"pose_{calls['count']}.pdbqt"
        pose_path.parent.mkdir(parents=True, exist_ok=True)
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1   "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        return DockingResult(
            success=True,
            log_text=f"   1       {affinity:.1f}      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    return fake_dock


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
    assert stats["coverage_bitmap_occupancy"] > 0.0


def test_run_loads_seeds_when_checkpoint_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    seeds = tmp_path / "seeds.smi"
    seeds.write_text("CCO zinc_1\n", encoding="utf-8")
    output_dir = tmp_path / "run"

    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(
        fuzzer_module,
        "dock",
        _make_successful_dock(output_dir),
    )

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=seeds,
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
    )

    assert stats["iterations"] == 0
    assert stats["corpus_size"] == 1
    assert stats["total_docks"] == 1
    assert _corpus_checkpoint(output_dir).exists()
    assert (output_dir / "corpus.json").exists()


def test_run_returns_hashed_coverage_metrics_in_stats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir, x=0.0, y=0.0, z=0.0))

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
    )

    assert stats["coverage_mode"] == "hashed_fingerprint"
    assert stats["coverage_bitmap_occupancy"] > 0.0
    assert stats["coverage_epoch"] == 0
    assert stats["novelty_strong_count"] == 1
    assert stats["novelty_weak_count"] == 0
    assert stats["novelty_none_count"] == 0


def test_run_treats_hashed_seed_novelty_as_interesting_even_without_new_union_bits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir, x=10.0, y=0.0, z=0.0))

    class FakeCoverageMap:
        def __init__(self, pocket_residue_ids, **kwargs) -> None:
            self.pocket_residue_ids = {int(value) for value in pocket_residue_ids}
            self.epoch = 0
            self.strong_novelty_count = 0
            self.weak_novelty_count = 0
            self.none_novelty_count = 0
            self._calls = 0

        def observe(self, fingerprint: frozenset[int]) -> CoverageObservation:
            self._calls += 1
            if self._calls == 1:
                self.strong_novelty_count += 1
                return CoverageObservation(
                    fingerprint=fingerprint,
                    fingerprint_mask=0,
                    hash_index=0,
                    novelty_class="strong",
                    novelty_score=2,
                    bitmap_occupancy=0.0,
                    epoch=0,
                )
            self.none_novelty_count += 1
            return CoverageObservation(
                fingerprint=fingerprint,
                fingerprint_mask=0,
                hash_index=0,
                novelty_class="none",
                novelty_score=0,
                bitmap_occupancy=0.0,
                epoch=0,
            )

        def update(self, fingerprint: frozenset[int]) -> frozenset[int]:
            self.observe(fingerprint)
            return frozenset()

        def bitmap_occupancy(self) -> float:
            return 0.0

        def stats(self) -> dict[str, float | int | str]:
            return {
                "coverage_mode": "hashed_fingerprint",
                "coverage_bitmap_occupancy": 0.0,
                "coverage_epoch": 0,
                "novelty_strong_count": self.strong_novelty_count,
                "novelty_weak_count": self.weak_novelty_count,
                "novelty_none_count": self.none_novelty_count,
            }

        def save(self, path: Path) -> None:
            path.write_text('{"pocket_residue_ids": [1, 2]}', encoding="utf-8")

        def load(self, path: Path) -> None:
            return

    monkeypatch.setattr(fuzzer_module, "CoverageMap", FakeCoverageMap)
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        logger=messages.append,
    )

    assert stats["corpus_size"] == 1
    assert any("interesting=1" in message and "fallback_added=0" in message for message in messages)


def test_run_parallelizes_seed_docking_when_workers_gt_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(
        fuzzer_module,
        "load_smiles",
        lambda _path: [("CCO", "seed_1"), ("CCN", "seed_2")],
    )
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    class FakeIterator:
        def __init__(self, results: list[tuple[int, DockingResult]]) -> None:
            self._results = results

        def next(self, timeout=None):  # noqa: ANN001 - matches multiprocessing iterator
            if not self._results:
                raise AssertionError("next() called with no pending seed results")
            return self._results.pop(0)

    class FakePool:
        def __init__(self) -> None:
            self.imap_calls = 0
            self.job_counts: list[int] = []
            self.closed = False
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, jobs):
            job_list = list(jobs)
            self.imap_calls += 1
            self.job_counts.append(len(job_list))
            results: list[tuple[int, DockingResult]] = []
            for idx, _pdbqt, _target_cfg, _exhaustiveness, _num_modes, _engine in job_list:
                pose_path = output_dir / f"seed_pool_pose_{idx}.pdbqt"
                pose_path.write_text(
                    "MODEL 1\n"
                    "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
                    "ENDMDL\n",
                    encoding="utf-8",
                )
                results.append(
                    (
                        idx,
                        DockingResult(
                            success=True,
                            log_text="   1       -8.0      0.000      0.000\n",
                            pose_path=str(pose_path),
                            error=None,
                            completed=True,
                        ),
                    )
                )
            return FakeIterator(results)

        def close(self) -> None:
            self.closed = True

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=2,
    )

    assert fake_pool.imap_calls == 1
    assert fake_pool.job_counts == [2]
    assert fake_pool.closed is True
    assert fake_pool.terminated is False
    assert fake_pool.joined is True
    assert stats["attempted_docks"] == 2
    assert stats["completed_docks"] == 2


def test_run_emits_progress_heartbeat_while_waiting_for_seed_pool_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    pose_path = output_dir / "seed_pool_pose_0.pdbqt"
    pose_path.write_text(
        "MODEL 1\n"
        "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
        "ENDMDL\n",
        encoding="utf-8",
    )
    result = (
        0,
        DockingResult(
            success=True,
            log_text="   1       -8.0      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
            completed=True,
        ),
    )

    class FakeIterator:
        def __init__(self) -> None:
            self.timeouts_remaining = 3
            self.sent = False

        def next(self, timeout=None):  # noqa: ANN001 - multiprocessing iterator compatibility
            if self.timeouts_remaining > 0:
                self.timeouts_remaining -= 1
                raise fuzzer_module.PoolTimeoutError()
            if self.sent:
                raise AssertionError("seed iterator consumed too many times")
            self.sent = True
            return result

    class FakePool:
        def __init__(self) -> None:
            self.closed = False
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            return FakeIterator()

        def close(self) -> None:
            self.closed = True

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    now = {"value": 0.0}

    def fake_monotonic() -> float:
        now["value"] += 0.6
        return now["value"]

    monkeypatch.setattr(fuzzer_module.time, "monotonic", fake_monotonic)

    statuses = []
    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=2,
        progress_callback=statuses.append,
    )

    seed_wait_updates = [
        status
        for status in statuses
        if status.stage == "seed_dock" and status.total_docks == 1 and status.completed_docks == 0
    ]
    assert len(seed_wait_updates) >= 2
    assert stats["completed_docks"] == 1
    assert fake_pool.closed is True
    assert fake_pool.terminated is False
    assert fake_pool.joined is True


def test_run_can_disable_progress_heartbeat_while_waiting_for_seed_pool_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    pose_path = output_dir / "seed_pool_pose_0.pdbqt"
    pose_path.write_text(
        "MODEL 1\n"
        "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
        "ENDMDL\n",
        encoding="utf-8",
    )
    result = (
        0,
        DockingResult(
            success=True,
            log_text="   1       -8.0      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
            completed=True,
        ),
    )

    class FakeIterator:
        def __init__(self) -> None:
            self.timeouts_remaining = 3
            self.sent = False

        def next(self, timeout=None):  # noqa: ANN001 - multiprocessing iterator compatibility
            if self.timeouts_remaining > 0:
                self.timeouts_remaining -= 1
                raise fuzzer_module.PoolTimeoutError()
            if self.sent:
                raise AssertionError("seed iterator consumed too many times")
            self.sent = True
            return result

    class FakePool:
        def __init__(self) -> None:
            self.closed = False
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            return FakeIterator()

        def close(self) -> None:
            self.closed = True

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    now = {"value": 0.0}

    def fake_monotonic() -> float:
        now["value"] += 0.6
        return now["value"]

    monkeypatch.setattr(fuzzer_module.time, "monotonic", fake_monotonic)

    statuses = []
    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=2,
        progress_callback=statuses.append,
        progress_heartbeat_seconds=0.0,
    )

    seed_wait_updates = [
        status
        for status in statuses
        if status.stage == "seed_dock" and status.total_docks == 1 and status.completed_docks == 0
    ]
    assert len(seed_wait_updates) == 0
    assert stats["completed_docks"] == 1
    assert fake_pool.closed is True
    assert fake_pool.terminated is False
    assert fake_pool.joined is True


def test_run_reports_attempted_vs_completed_docks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(
        fuzzer_module,
        "load_smiles",
        lambda _path: [("CCO", "seed_ok"), ("CCN", "seed_fail")],
    )
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    def fake_dock(*args, **kwargs) -> DockingResult:
        calls = fake_dock.calls
        fake_dock.calls += 1
        if calls == 0:
            pose_path = output_dir / "seed_pose_1.pdbqt"
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
                completed=True,
            )
        return DockingResult(
            success=False,
            log_text="error while loading shared libraries: libcudnn.so.9",
            pose_path=None,
            error="Docking failed with return code 127",
            completed=False,
        )

    fake_dock.calls = 0
    monkeypatch.setattr(fuzzer_module, "dock", fake_dock)
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        logger=messages.append,
    )

    assert stats["total_docks"] == 2
    assert stats["attempted_docks"] == 2
    assert stats["completed_docks"] == 1


def test_run_uses_seed_fallback_when_interesting_pool_is_too_small(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(
        fuzzer_module,
        "load_smiles",
        lambda _path: [("CCO", "seed_interesting"), ("CCN", "seed_boring")],
    )
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    dock_plan = [
        {"affinity": -8.0, "x": 0.0},
        {"affinity": -7.0, "x": 10.0},
    ]

    def fake_dock(*args, **kwargs) -> DockingResult:
        call = dock_plan.pop(0)
        idx = 2 - len(dock_plan)
        pose_path = output_dir / f"seed_pose_{idx}.pdbqt"
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1   "
            f"{call['x']:8.3f}{0.0:8.3f}{0.0:8.3f}  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        return DockingResult(
            success=True,
            log_text=f"   1       {call['affinity']:.1f}      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    monkeypatch.setattr(fuzzer_module, "dock", fake_dock)
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        logger=messages.append,
    )

    corpus = Corpus()
    corpus.load(_corpus_checkpoint(output_dir))

    assert stats["total_docks"] == 2
    assert stats["corpus_size"] == 2
    assert corpus.size() == 2
    assert {corpus.pop().source_id, corpus.pop().source_id} == {
        "seed_interesting",
        "seed_boring",
    }
    assert any(message.startswith("[SEED][FALLBACK]") for message in messages)


def test_run_retries_seed_preparation_without_druglike_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])

    prepare_calls: list[bool] = []

    def fake_prepare(_smiles: str, **kwargs) -> str | None:
        require_drug_like = kwargs.get("require_drug_like", True)
        prepare_calls.append(require_drug_like)
        return None if require_drug_like else "PDBQT"

    monkeypatch.setattr(fuzzer_module, "prepare_smiles", fake_prepare)
    monkeypatch.setattr(
        fuzzer_module,
        "dock",
        _make_successful_dock(output_dir, affinity=-7.0, x=10.0, y=0.0, z=0.0),
    )
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        logger=messages.append,
    )

    assert stats["total_docks"] == 1
    assert stats["corpus_size"] == 1
    assert prepare_calls == [True, False]
    assert any("prepare_relaxed=1" in message for message in messages)


def test_run_handles_keyboard_interrupt_during_seed_docking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(
        fuzzer_module,
        "load_smiles",
        lambda _path: [("CCO", "seed_1"), ("CCN", "seed_2")],
    )
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    dock_calls: list[str] = []

    def interrupting_dock(*args, **kwargs) -> DockingResult:
        dock_calls.append("called")
        raise KeyboardInterrupt

    monkeypatch.setattr(fuzzer_module, "dock", interrupting_dock)
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=1,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert stats["total_docks"] == 1
    assert len(dock_calls) == 1
    assert (output_dir / "coverage.json").exists()
    assert _corpus_checkpoint(output_dir).exists()
    assert any("manual quit requested" in message for message in messages)


def test_run_skips_mismatched_coverage_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    CoverageMap({7, 8}).save(output_dir / "coverage.json")
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")

    seeds = tmp_path / "seeds.smi"
    seeds.write_text("CCO zinc_1\n", encoding="utf-8")
    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(
        fuzzer_module,
        "dock",
        _make_successful_dock(output_dir, affinity=-7.0, x=10.0, y=0.0, z=0.0),
    )

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=seeds,
        output_dir=output_dir,
        max_iterations=0,
        workers=1,
        logger=messages.append,
    )

    assert stats["coverage_bitmap_occupancy"] > 0.0
    assert stats["corpus_size"] == 1
    assert any("skipping coverage checkpoint" in message.lower() for message in messages)
    assert any(message.startswith("[SEED][FALLBACK]") for message in messages)


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
    assert len(pose_paths) == 4
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

    assert stats["hits"] == 2
    assert docking_calls == [4, 16, 4, 16]
    assert len(findings_dirs) == 2
    assert all((path / "pose.pdbqt").exists() for path in findings_dirs)


def test_run_confirms_seed_hits_before_saving_findings(
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
        max_iterations=0,
        workers=1,
        exhaustiveness=4,
        exhaustiveness_confirm=16,
    )

    findings_dirs = list((output_dir / "findings").iterdir())

    assert stats["hits"] == 1
    assert stats["total_docks"] == 2
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
        target_name = getattr(target, "name", "direct")
        dock_calls.append(target_name)
        pose_path = output_dir / f"pose_{len(dock_calls)}.pdbqt"
        pose_path.write_text(
            "MODEL 1\n"
            "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
            "ENDMDL\n",
            encoding="utf-8",
        )
        affinity = -10.0 if target_name == "mini" else -3.0
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
    assert stats["total_docks"] == 4


def test_run_terminates_worker_pool_and_saves_checkpoints_on_manual_quit(
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
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))

    class FakePool:
        def __init__(self) -> None:
            self.closed = False
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            raise KeyboardInterrupt

        def close(self) -> None:
            self.closed = True

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=2,
        mutations_per_entry=1,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert fake_pool.terminated is True
    assert fake_pool.joined is True
    assert fake_pool.closed is False
    assert (output_dir / "coverage.json").exists()
    assert _corpus_checkpoint(output_dir).exists()


def test_run_reports_failed_reason_and_persists_checkpoints(
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
    monkeypatch.setattr(
        fuzzer_module,
        "mutate",
        lambda _smiles, n=20, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=1,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "failed"
    assert stats["failure_reason"] == "RuntimeError: boom"
    assert any("[FAIL] campaign failed" in message for message in messages)
    assert (output_dir / "coverage.json").exists()
    assert _corpus_checkpoint(output_dir).exists()


def test_run_handles_keyboard_interrupt_while_waiting_for_pool_results(
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
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))

    class FakeIterator:
        def __init__(self) -> None:
            self.calls = 0

        def next(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise fuzzer_module.PoolTimeoutError()
            raise KeyboardInterrupt

    class FakePool:
        def __init__(self) -> None:
            self.closed = False
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            return FakeIterator()

        def close(self) -> None:
            self.closed = True

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=2,
        mutations_per_entry=1,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert fake_pool.terminated is True
    assert fake_pool.joined is True


def test_run_treats_pool_broken_pipe_during_seed_submission_as_manual_abort(
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
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    class FakePool:
        def __init__(self) -> None:
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            raise BrokenPipeError("simulated broken pipe during seed submission")

        def close(self) -> None:
            return

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=2,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert fake_pool.terminated is True
    assert fake_pool.joined is True
    assert any("pool result channel closed during docking collection" in message for message in messages)


def test_run_keeps_manual_abort_reason_when_pool_shutdown_raises(
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
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))

    class FakePool:
        def imap_unordered(self, _fn, _jobs):
            raise KeyboardInterrupt

        def close(self) -> None:
            return

        def terminate(self) -> None:
            raise BrokenPipeError("simulated pipe break during terminate")

        def join(self) -> None:
            raise BrokenPipeError("simulated pipe break during join")

    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: FakePool())

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=2,
        mutations_per_entry=1,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert "failure_reason" not in stats
    assert not any("worker pool shutdown step failed" in message for message in messages)
    assert not any("worker pool join failed" in message for message in messages)


def test_run_treats_pool_broken_pipe_during_mutation_submission_as_manual_abort(
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
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))

    class FakePool:
        def __init__(self) -> None:
            self.calls = 0
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            self.calls += 1
            if self.calls == 1:
                # Seed submission: return one completed seed result.
                class SeedIterator:
                    def __init__(self) -> None:
                        self._sent = False

                    def next(self, timeout=None):  # noqa: ANN001 - multiprocessing compatibility
                        if self._sent:
                            raise AssertionError("seed iterator consumed too many times")
                        self._sent = True
                        pose_path = output_dir / "seed_pose.pdbqt"
                        pose_path.write_text(
                            "MODEL 1\n"
                            "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
                            "ENDMDL\n",
                            encoding="utf-8",
                        )
                        return (
                            0,
                            DockingResult(
                                success=True,
                                log_text="   1       -8.0      0.000      0.000\n",
                                pose_path=str(pose_path),
                                error=None,
                                completed=True,
                            ),
                        )

                return SeedIterator()
            raise BrokenPipeError("simulated broken pipe during mutation submission")

        def close(self) -> None:
            return

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=2,
        mutations_per_entry=1,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert fake_pool.terminated is True
    assert fake_pool.joined is True
    assert any("pool result channel closed during docking collection" in message for message in messages)


def test_run_treats_pool_broken_pipe_during_result_collection_as_manual_abort(
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
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))

    class FakeIterator:
        def next(self, timeout=None):
            raise BrokenPipeError("simulated broken pipe while receiving worker result")

    class FakePool:
        def __init__(self) -> None:
            self.terminated = False
            self.joined = False

        def imap_unordered(self, _fn, _jobs):
            return FakeIterator()

        def close(self) -> None:
            return

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            self.joined = True

    fake_pool = FakePool()
    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=2,
        mutations_per_entry=1,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert fake_pool.terminated is True
    assert fake_pool.joined is True
    assert any("pool result channel closed during docking collection" in message for message in messages)


def test_run_preserves_manual_abort_when_final_checkpoint_save_fails(
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
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))
    monkeypatch.setattr(
        fuzzer_module,
        "_save_corpus_checkpoints",
        lambda _corpus, _paths: (_ for _ in ()).throw(BrokenPipeError("save broke")),
    )

    class FakePool:
        def imap_unordered(self, _fn, _jobs):
            raise KeyboardInterrupt

        def close(self) -> None:
            return

        def terminate(self) -> None:
            return

        def join(self) -> None:
            return

    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: FakePool())

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=1,
        workers=2,
        mutations_per_entry=1,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert "failure_reason" not in stats
    assert any("failed to save final checkpoints after manual abort" in message for message in messages)


def test_run_uses_single_chunk_pool_dispatch_for_seed_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")

    class FakeIterator:
        def __init__(self, result: tuple[int, DockingResult]) -> None:
            self._result = result
            self._sent = False

        def next(self, timeout=None):  # noqa: ANN001 - multiprocessing iterator compatibility
            if self._sent:
                raise AssertionError("seed iterator consumed too many times")
            self._sent = True
            return self._result

    class FakePool:
        def __init__(self) -> None:
            self.chunksizes: list[int | None] = []

        def imap_unordered(self, _fn, jobs, chunksize=None):
            self.chunksizes.append(chunksize)
            job_list = list(jobs)
            idx, _pdbqt, _target_cfg, _exhaustiveness, _num_modes, _engine = job_list[0]
            pose_path = output_dir / "seed_pose.pdbqt"
            pose_path.write_text(
                "MODEL 1\n"
                "ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00  0.000 C\n"
                "ENDMDL\n",
                encoding="utf-8",
            )
            return FakeIterator(
                (
                    idx,
                    DockingResult(
                        success=True,
                        log_text="   1       -8.0      0.000      0.000\n",
                        pose_path=str(pose_path),
                        error=None,
                        completed=True,
                    ),
                )
            )

        def close(self) -> None:
            return

        def terminate(self) -> None:
            return

        def join(self) -> None:
            return

    fake_pool = FakePool()
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=2,
    )

    assert stats["attempted_docks"] == 1
    assert stats["completed_docks"] == 1
    assert fake_pool.chunksizes == [1]


def test_run_falls_back_to_single_worker_when_pool_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(fuzzer_module, "dock", _make_successful_dock(output_dir))
    messages: list[str] = []

    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=4,
        logger=messages.append,
    )

    assert stats["stopped_reason"] == "max_iterations"
    assert stats["attempted_docks"] == 1
    assert stats["completed_docks"] == 1
    assert any("failed to start worker pool" in message for message in messages)


def test_run_does_not_block_when_pool_join_hangs_after_manual_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receptor = tmp_path / "protein.pdbqt"
    receptor.write_text("ATOM      1  N   MET A   1       0.0   0.0   0.0\n", encoding="utf-8")
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    monkeypatch.setattr(fuzzer_module, "load_smiles", lambda _path: [("CCO", "seed_1")])
    monkeypatch.setattr(fuzzer_module, "prepare_smiles", lambda smiles, **kwargs: "PDBQT")
    monkeypatch.setattr(fuzzer_module, "POOL_ABORT_JOIN_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(fuzzer_module, "POOL_ABORT_JOIN_GRACE_SECONDS", 0.02)

    class FakeWorker:
        def __init__(self) -> None:
            self.alive = True
            self.killed = False

        def is_alive(self) -> bool:
            return self.alive

        def kill(self) -> None:
            self.killed = True
            self.alive = False

    class FakePool:
        def __init__(self) -> None:
            self.terminated = False
            self._pool = [FakeWorker(), FakeWorker()]

        def imap_unordered(self, _fn, _jobs):
            raise KeyboardInterrupt

        def close(self) -> None:
            return

        def terminate(self) -> None:
            self.terminated = True

        def join(self) -> None:
            time.sleep(2.0)

    fake_pool = FakePool()
    messages: list[str] = []
    monkeypatch.setattr(fuzzer_module, "Pool", lambda *args, **kwargs: fake_pool)

    start = time.monotonic()
    stats = run(
        target_config=_target_config(receptor),
        seed_smiles_path=tmp_path / "seeds.smi",
        output_dir=output_dir,
        max_iterations=0,
        workers=2,
        logger=messages.append,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert stats["stopped_reason"] == "keyboard_interrupt"
    assert fake_pool.terminated is True
    assert all(worker.killed for worker in fake_pool._pool)
    assert any("worker pool join timed out" in message for message in messages)
