from __future__ import annotations

from multiprocessing import Pool, TimeoutError as PoolTimeoutError
from pathlib import Path
import json
import errno
import signal
import sys
import threading
import time
import traceback
from typing import Any, Callable, Mapping

from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.coverage import CoverageMap, checkpoint_pocket_residue_ids
from biofuzz.core.tui import RuntimeStatus
from biofuzz.docking.config import TargetConfig, normalize_coverage_config
from biofuzz.docking.parser import parse_log, parse_pose
from biofuzz.docking.runner import DockingResult, dock
from biofuzz.molecules import mutator as mutator_module
from biofuzz.molecules.mutator import MutationCandidate, mutate, mutate_with_metadata
from biofuzz.molecules.preparation import prepare_smiles
from biofuzz.oracle.affinity import evaluate
from biofuzz.protein.pocket import compute_fingerprint
from biofuzz.protein.residues import load_residue_coordinates
from biofuzz.storage.cache import PDBQTCache
from biofuzz.storage.findings import FindingsStore

SEED_FALLBACK_MIN_INTERESTING = 64
SEED_FALLBACK_TOP_K = 256
SEED_DOCK_BATCH_MULTIPLIER = 4
POOL_DOCK_CHUNKSIZE = 1
POOL_RESULT_POLL_TIMEOUT_SECONDS = 0.2
PROGRESS_HEARTBEAT_SECONDS = 1.0
POOL_ABORT_JOIN_TIMEOUT_SECONDS = 2.0
POOL_ABORT_JOIN_GRACE_SECONDS = 0.5


def _confirmation_requested(exhaustiveness_confirm: int | None) -> bool:
    return exhaustiveness_confirm is not None and exhaustiveness_confirm > 0


def load_smiles(seed_smiles_path: str | Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    with Path(seed_smiles_path).open("r", encoding="utf-8") as fh:
        for idx, raw in enumerate(fh):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            smiles = parts[0]
            source_id = parts[1] if len(parts) > 1 else f"seed_{idx}"
            entries.append((smiles, source_id))
    return entries


def compute_priority(new_bits: int, affinity: float, times_mutated: int) -> float:
    return compute_priority_weighted(
        new_bits=new_bits,
        affinity=affinity,
        times_mutated=times_mutated,
        priority_new_bit_weight=10.0,
        priority_affinity_weight=1.0,
        priority_reuse_penalty=0.1,
    )


def compute_priority_weighted(
    new_bits: int,
    affinity: float,
    times_mutated: int,
    priority_new_bit_weight: float = 10.0,
    priority_affinity_weight: float = 1.0,
    priority_reuse_penalty: float = 0.1,
    novelty_score: int | None = None,
) -> float:
    coverage_signal = novelty_score if novelty_score is not None else new_bits
    return (
        (coverage_signal * priority_new_bit_weight)
        + (max(0.0, -affinity - 5.0) * priority_affinity_weight)
        - (times_mutated * priority_reuse_penalty)
    )


def coverage_signal_for_entry(entry: CorpusEntry) -> int:
    if entry.novelty_score is not None:
        return entry.novelty_score
    return entry.new_bits


def score_corpus_entry(
    entry: CorpusEntry,
    priority_new_bit_weight: float = 10.0,
    priority_affinity_weight: float = 1.0,
    priority_reuse_penalty: float = 0.1,
) -> float:
    affinity = entry.best_affinity if entry.best_affinity is not None else -5.0
    find_bonus = float(entry.finds * 5)
    return max(
        0.1,
        compute_priority_weighted(
            new_bits=entry.new_bits,
            affinity=affinity,
            times_mutated=entry.times_mutated,
            priority_new_bit_weight=priority_new_bit_weight,
            priority_affinity_weight=priority_affinity_weight,
            priority_reuse_penalty=priority_reuse_penalty,
            novelty_score=coverage_signal_for_entry(entry),
        )
        + find_bonus,
    )


def compute_power_score(entry: CorpusEntry) -> float:
    affinity = entry.best_affinity if entry.best_affinity is not None else -5.0
    affinity_bonus = max(0.0, -affinity - 5.0)
    coverage_signal = coverage_signal_for_entry(entry)
    return max(
        1.0,
        1.0
        + (coverage_signal * 2.0)
        + affinity_bonus
        + (entry.finds * 6.0)
        - (entry.times_mutated * 0.2),
    )


def compute_mutation_budget(entry: CorpusEntry, base_mutations: int) -> tuple[float, int]:
    power_score = compute_power_score(entry)
    factor = 1.0
    if power_score >= 24.0:
        factor = 3.0
    elif power_score >= 12.0:
        factor = 2.0
    elif power_score >= 6.0:
        factor = 1.5
    budget = max(1, int(round(base_mutations * factor)))
    return power_score, budget


def _mutate_candidates(
    smiles: str,
    n: int,
    min_mw: float,
    max_mw: float,
    max_logp: float,
    max_hbd: int,
    max_hba: int,
    max_rot_bonds: int,
) -> list[MutationCandidate]:
    if mutate is not mutator_module.mutate:
        legacy_mutants = mutate(
            smiles,
            n=n,
            min_mw=min_mw,
            max_mw=max_mw,
            max_logp=max_logp,
            max_hbd=max_hbd,
            max_hba=max_hba,
            max_rot_bonds=max_rot_bonds,
        )
        return [
            candidate
            if isinstance(candidate, MutationCandidate)
            else MutationCandidate(
                smiles=str(candidate),
                stage="havoc",
                mutation_type="legacy_mutate",
            )
            for candidate in legacy_mutants
        ]

    return mutate_with_metadata(
        smiles,
        n=n,
        min_mw=min_mw,
        max_mw=max_mw,
        max_logp=max_logp,
        max_hbd=max_hbd,
        max_hba=max_hba,
        max_rot_bonds=max_rot_bonds,
    )


def _dock_worker(payload: tuple[int, str, TargetConfig, int, int, str]) -> tuple[int, DockingResult]:
    idx, pdbqt, target_config, exhaustiveness, num_modes, engine = payload
    try:
        result = dock(
            pdbqt,
            target_config,
            exhaustiveness=exhaustiveness,
            num_modes=num_modes,
            engine=engine,
        )
    except KeyboardInterrupt:
        # Suppress worker tracebacks when the campaign is manually aborted.
        result = DockingResult(
            success=False,
            log_text="",
            pose_path=None,
            error="Docking interrupted by user",
            completed=False,
        )
    return idx, result


def _pool_worker_init_ignore_sigint() -> None:
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:
        # Not in main thread of interpreter.
        pass
    # Pool teardown can race with worker queue writes during manual abort. Suppress the
    # resulting BrokenPipe traceback noise from multiprocessing internals in workers only.
    if not getattr(traceback, "_biofuzz_broken_pipe_suppressed", False):
        original_print_exc = traceback.print_exc

        def quiet_print_exc(*args, **kwargs):  # type: ignore[no-untyped-def]
            _typ, exc, _tb = sys.exc_info()
            if isinstance(exc, BrokenPipeError):
                return
            return original_print_exc(*args, **kwargs)

        traceback.print_exc = quiet_print_exc  # type: ignore[assignment]
        setattr(traceback, "_biofuzz_broken_pipe_suppressed", True)


def _cleanup_pose_path(pose_path: str | None) -> None:
    if pose_path:
        Path(pose_path).unlink(missing_ok=True)


def _load_saved_pocket_residue_ids(
    path: Path,
    current_pocket_residue_ids: set[str] | None = None,
) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return checkpoint_pocket_residue_ids(payload, current_pocket_residue_ids)


def _corpus_checkpoint_paths(output_dir: Path) -> tuple[Path, Path]:
    return output_dir / "corpus" / "state.json", output_dir / "corpus.json"


def _load_corpus_checkpoint(corpus: Corpus, checkpoint_paths: tuple[Path, Path]) -> Path | None:
    primary_checkpoint, legacy_checkpoint = checkpoint_paths
    if primary_checkpoint.exists():
        corpus.load(primary_checkpoint)
        return primary_checkpoint
    if legacy_checkpoint.exists():
        corpus.load(legacy_checkpoint)
        return legacy_checkpoint
    return None


def _save_corpus_checkpoints(corpus: Corpus, checkpoint_paths: tuple[Path, Path]) -> None:
    primary_checkpoint, legacy_checkpoint = checkpoint_paths
    corpus.save(primary_checkpoint)
    corpus.save(legacy_checkpoint)


def _dock_completed(result: DockingResult) -> bool:
    if result.completed is not None:
        return result.completed
    return result.success


def _pool_imap_unordered_single_chunk(
    pool: object,
    jobs: list[tuple[int, str, TargetConfig, int, int, str]],
):
    try:
        return pool.imap_unordered(_dock_worker, jobs, chunksize=POOL_DOCK_CHUNKSIZE)  # type: ignore[attr-defined]
    except TypeError as exc:
        # Test doubles may expose `imap_unordered(fn, jobs)` without chunksize support.
        if "chunksize" not in str(exc):
            raise
        return pool.imap_unordered(_dock_worker, jobs)  # type: ignore[attr-defined]


def _start_pool_join_thread(pool: object) -> tuple[threading.Event, list[BaseException]]:
    done = threading.Event()
    errors: list[BaseException] = []

    def join_target() -> None:
        try:
            pool.join()  # type: ignore[attr-defined]
        except BaseException as exc:  # pragma: no cover - propagated through `errors`.
            errors.append(exc)
        finally:
            done.set()

    threading.Thread(
        target=join_target,
        name="biofuzz_pool_join",
        daemon=True,
    ).start()
    return done, errors


def _force_kill_pool_workers(pool: object) -> int:
    workers = getattr(pool, "_pool", None)
    if workers is None:
        return 0

    killed = 0
    for worker in workers:
        try:
            if hasattr(worker, "is_alive") and not worker.is_alive():
                continue
            if hasattr(worker, "kill"):
                worker.kill()
                killed += 1
                continue
            if hasattr(worker, "terminate"):
                worker.terminate()
                killed += 1
        except Exception:
            continue
    return killed


def run(
    target_config: TargetConfig,
    seed_smiles_path: str | Path,
    output_dir: str | Path,
    max_iterations: int | None = None,
    workers: int = 1,
    checkpoint_every: int = 500,
    mutations_per_entry: int = 20,
    molecule_min_mw: float = 0.0,
    molecule_max_mw: float = 550.0,
    molecule_max_logp: float = 5.0,
    molecule_max_hbd: int = 5,
    molecule_max_hba: int = 10,
    molecule_max_rot_bonds: int = 10,
    exhaustiveness: int = 4,
    exhaustiveness_confirm: int | None = 16,
    num_modes: int = 3,
    engine: str = "gnina",
    max_corpus_size: int | None = None,
    priority_new_bit_weight: float = 10.0,
    priority_affinity_weight: float = 1.0,
    priority_reuse_penalty: float = 0.1,
    coverage_config: Mapping[str, Any] | None = None,
    logger: Callable[[str], None] | None = None,
    progress_callback: Callable[[RuntimeStatus], None] | None = None,
    progress_heartbeat_seconds: float = PROGRESS_HEARTBEAT_SECONDS,
) -> dict[str, float | int | str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    resolved_coverage_config = normalize_coverage_config(coverage_config)
    progress_heartbeat_seconds = max(0.0, progress_heartbeat_seconds)

    def log(message: str) -> None:
        if logger:
            logger(message)

    start_time = time.monotonic()

    def elapsed_seconds() -> float:
        return max(0.0, time.monotonic() - start_time)

    current_stage = "init"
    current_mutation_stage = "-"
    current_mutation_type: str | None = None
    current_parent: str | None = None
    current_smiles: str | None = None
    current_power_score = 1.0
    current_budget = mutations_per_entry
    checkpoint_count = 0
    attempted_docks = 0
    completed_docks = 0
    first_attempt_elapsed_seconds: float | None = None
    last_completed_dock_elapsed_seconds: float | None = None
    runtime_gpu_active: bool | None = None
    last_progress_emit_elapsed_seconds = -float("inf")

    def record_dock_attempts(count: int = 1) -> None:
        nonlocal attempted_docks, first_attempt_elapsed_seconds
        if count <= 0:
            return
        attempted_docks += count
        if first_attempt_elapsed_seconds is None:
            first_attempt_elapsed_seconds = elapsed_seconds()

    def record_dock_completion(result: DockingResult) -> None:
        nonlocal completed_docks, last_completed_dock_elapsed_seconds, runtime_gpu_active
        if result.gpu_active is True:
            runtime_gpu_active = True
        elif result.gpu_active is False and runtime_gpu_active is None:
            runtime_gpu_active = False
        if _dock_completed(result):
            completed_docks += 1
            last_completed_dock_elapsed_seconds = elapsed_seconds()

    def emit_progress(force_stage: str | None = None) -> None:
        nonlocal last_progress_emit_elapsed_seconds
        if progress_callback is None:
            return
        elapsed = elapsed_seconds()
        last_progress_emit_elapsed_seconds = elapsed
        completed_dock_staleness_seconds: float | None = None
        if attempted_docks > 0:
            if completed_docks > 0 and last_completed_dock_elapsed_seconds is not None:
                completed_dock_staleness_seconds = max(
                    0.0,
                    elapsed - last_completed_dock_elapsed_seconds,
                )
            elif first_attempt_elapsed_seconds is not None:
                completed_dock_staleness_seconds = max(
                    0.0,
                    elapsed - first_attempt_elapsed_seconds,
                )
        progress_callback(
            RuntimeStatus(
                stage=force_stage or current_stage,
                mutation_stage=current_mutation_stage,
                mutation_type=current_mutation_type,
                current_parent=current_parent,
                current_smiles=current_smiles,
                power_score=current_power_score,
                mutation_budget=current_budget,
                total_docks=attempted_docks,
                completed_docks=completed_docks,
                docks_per_sec=(completed_docks / elapsed) if completed_docks else 0.0,
                completed_dock_staleness_seconds=completed_dock_staleness_seconds,
                corpus_size=corpus.size(),
                finds=hits,
                coverage_bitmap_occupancy=cov_map.bitmap_occupancy(),
                coverage_epoch=cov_map.epoch,
                novelty_strong_count=cov_map.strong_novelty_count,
                novelty_weak_count=cov_map.weak_novelty_count,
                novelty_none_count=cov_map.none_novelty_count,
                best_affinity=best_affinity,
                checkpoints=checkpoint_count,
                elapsed_seconds=elapsed,
                gpu_active=runtime_gpu_active,
            )
        )

    def emit_progress_heartbeat(force_stage: str | None = None) -> None:
        if progress_callback is None:
            return
        if progress_heartbeat_seconds <= 0.0:
            return
        if elapsed_seconds() - last_progress_emit_elapsed_seconds < progress_heartbeat_seconds:
            return
        emit_progress(force_stage)

    def observe_extra_dock(result: DockingResult) -> None:
        record_dock_attempts()
        record_dock_completion(result)
        emit_progress()

    def record_selectivity_status(status: str) -> None:
        if status in selectivity_counts:
            selectivity_counts[status] += 1

    def evaluate_and_process_hit(
        *,
        smiles: str,
        ligand_pdbqt: str,
        modes,
        pose_text: str,
        initial_pose_path: str,
        initial_affinity: float,
        oracle_stage: str = "oracle",
        confirm_stage: str = "confirm",
    ):
        nonlocal hits
        nonlocal current_stage

        current_stage = oracle_stage
        emit_progress()

        confirmation_requested = _confirmation_requested(exhaustiveness_confirm)

        preliminary_verdict = evaluate(
            modes,
            pose_text,
            target_config,
            smiles=smiles,
            ligand_pdbqt=ligand_pdbqt,
            selectivity_exhaustiveness=selectivity_exhaustiveness,
            num_modes=num_modes,
            docking_engine=engine,
            dock_observer=observe_extra_dock,
            check_selectivity=not confirmation_requested,
        )

        if not preliminary_verdict.is_hit:
            record_selectivity_status(preliminary_verdict.selectivity_status)
            return preliminary_verdict

        confirmed_verdict = preliminary_verdict
        confirmed_affinity = initial_affinity
        confirmed_pose_path = initial_pose_path

        if confirmation_requested:
            confirmed_verdict = preliminary_verdict.__class__(
                is_hit=False,
                affinity=initial_affinity,
                passed_tiers=list(preliminary_verdict.passed_tiers),
                selectivity_status=preliminary_verdict.selectivity_status,
                notes="Confirmation docking did not reproduce the preliminary target hit",
            )
            record_dock_attempts()
            current_stage = confirm_stage
            emit_progress()
            confirm_result = dock(
                ligand_pdbqt,
                target_config,
                exhaustiveness=exhaustiveness_confirm,
                num_modes=num_modes,
                engine=engine,
            )
            record_dock_completion(confirm_result)
            try:
                if confirm_result.success and confirm_result.pose_path:
                    confirm_pose_file = Path(confirm_result.pose_path)
                    if confirm_pose_file.exists():
                        confirm_pose_text = confirm_pose_file.read_text(encoding="utf-8")
                        confirm_modes = parse_log(confirm_result.log_text)
                        if confirm_modes:
                            confirmed_affinity = confirm_modes[0].affinity
                            confirmed_verdict = evaluate(
                                confirm_modes,
                                confirm_pose_text,
                                target_config,
                                smiles=smiles,
                                ligand_pdbqt=ligand_pdbqt,
                                selectivity_exhaustiveness=selectivity_exhaustiveness,
                                num_modes=num_modes,
                                docking_engine=engine,
                                dock_observer=observe_extra_dock,
                                check_selectivity=True,
                            )
                            if confirmed_verdict.is_hit:
                                confirmed_pose_path = confirm_result.pose_path
            finally:
                if confirmed_pose_path != confirm_result.pose_path:
                    _cleanup_pose_path(confirm_result.pose_path)

        if confirmed_verdict.is_hit and confirmed_pose_path:
            findings.save(
                smiles,
                confirmed_verdict,
                confirmed_pose_path,
                confirmed_affinity,
            )
            hits += 1
            log(
                "[HIT] {} | affinity={:.2f} | confirmed_exhaustiveness={}"
                .format(smiles, confirmed_affinity, exhaustiveness_confirm)
            )
            if confirmed_pose_path != initial_pose_path:
                _cleanup_pose_path(confirmed_pose_path)
        elif confirmed_pose_path and confirmed_pose_path != initial_pose_path:
            _cleanup_pose_path(confirmed_pose_path)

        record_selectivity_status(confirmed_verdict.selectivity_status)
        return confirmed_verdict

    def is_pool_pipe_error(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, EOFError, ConnectionResetError)):
            return True
        if isinstance(exc, OSError) and getattr(exc, "errno", None) in {
            errno.EPIPE,
            errno.ECONNRESET,
            errno.EBADF,
        }:
            return True
        if isinstance(exc, ValueError) and "closed" in str(exc).lower():
            return True
        return False

    def raise_manual_abort_on_pool_pipe_error(exc: BaseException) -> None:
        if is_pool_pipe_error(exc):
            log(
                "[WARN] pool result channel closed during docking collection; "
                "treating as manual abort"
            )
            raise KeyboardInterrupt from exc

    corpus = Corpus(max_size=max_corpus_size)
    cov_map = CoverageMap(
        target_config.pocket.residue_ids,
        enabled=bool(resolved_coverage_config["enabled"]),
        mode=str(resolved_coverage_config["mode"]),
        map_size_bytes=int(resolved_coverage_config["map_size_kib"]) * 1024,
        occupancy_rotate_threshold=float(
            resolved_coverage_config["occupancy_rotate_threshold"]
        ),
        novelty_weights=resolved_coverage_config["novelty_weights"],
    )
    findings = FindingsStore(output / "findings")
    cache = PDBQTCache(output / "cache")
    coverage_checkpoint = output / "coverage.json"
    corpus_checkpoint_paths = _corpus_checkpoint_paths(output)

    if coverage_checkpoint.exists():
        try:
            saved_pocket_ids = _load_saved_pocket_residue_ids(
                coverage_checkpoint,
                cov_map.pocket_residue_ids,
            )
            if saved_pocket_ids != cov_map.pocket_residue_ids:
                log(
                    "[WARN] skipping coverage checkpoint {} because pocket residues differ"
                    .format(coverage_checkpoint)
                )
            else:
                cov_map.load(coverage_checkpoint)
                log(f"[RESUME] loaded coverage checkpoint: {coverage_checkpoint}")
        except Exception as exc:
            log(f"[WARN] failed to load coverage checkpoint {coverage_checkpoint}: {exc}")

    try:
        loaded_corpus_checkpoint = _load_corpus_checkpoint(corpus, corpus_checkpoint_paths)
        if loaded_corpus_checkpoint is not None:
            corpus.configure_max_size(max_corpus_size)
            log(f"[RESUME] loaded corpus checkpoint: {loaded_corpus_checkpoint}")
    except Exception as exc:
        primary_corpus_checkpoint, legacy_corpus_checkpoint = corpus_checkpoint_paths
        log(
            "[WARN] failed to load corpus checkpoint {} or {}: {}"
            .format(primary_corpus_checkpoint, legacy_corpus_checkpoint, exc)
        )

    protein_residues = {}
    receptor_path = Path(target_config.receptor)
    if receptor_path.exists():
        protein_residues = load_residue_coordinates(receptor_path)
    else:
        log(f"[WARN] receptor not found for coverage parsing: {receptor_path}")

    iterations = 0
    hits = 0
    best_affinity: float | None = None
    stopped_reason = "completed"
    failure_reason: str | None = None
    selectivity_counts = {
        "passed": 0,
        "failed": 0,
        "skipped_unavailable": 0,
        "not_configured": 0,
    }
    selectivity_exhaustiveness = max(
        8,
        exhaustiveness_confirm if exhaustiveness_confirm is not None else exhaustiveness,
    )
    pool_needs_terminate = False
    previous_sigint_handler: int | Callable[[int, object], object] | None = None
    sigint_guard_installed = False
    abort_requested = False

    def install_sigint_guard() -> None:
        nonlocal previous_sigint_handler, sigint_guard_installed
        if sigint_guard_installed:
            return

        def guarded_sigint(_signum: int, _frame: object) -> None:
            nonlocal abort_requested
            abort_requested = True
            raise KeyboardInterrupt

        try:
            previous_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, guarded_sigint)
            sigint_guard_installed = True
        except ValueError:
            sigint_guard_installed = False

    def suppress_sigint() -> None:
        nonlocal abort_requested
        abort_requested = True
        if not sigint_guard_installed:
            return
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except ValueError:
            pass

    install_sigint_guard()
    pool = None
    if workers > 1:
        try:
            pool = Pool(processes=workers, initializer=_pool_worker_init_ignore_sigint)
        except Exception as exc:
            log(
                "[WARN] failed to start worker pool with workers={}; "
                "continuing in single-worker mode: {}: {}"
                .format(workers, type(exc).__name__, exc)
            )

    if corpus.size() == 0:
        try:
            seed_total = 0
            seed_interesting = 0
            seed_prepare_failed = 0
            seed_prepare_relaxed = 0
            seed_dock_failed = 0
            seed_parse_failed = 0
            seed_non_interesting = 0
            seed_fallback_added = 0
            seed_duplicates_skipped = 0
            seed_attempted = 0
            seed_completed = 0
            seed_successful_candidates: list[tuple[float, str, str, int, int]] = []
            interesting_smiles: set[str] = set()
            seen_seed_smiles: set[str] = set()
            seed_pending: list[tuple[str, str, str]] = []
            seed_dock_batch_size = max(1, workers * SEED_DOCK_BATCH_MULTIPLIER)

            def process_seed_result(
                smiles: str,
                source_id: str,
                ligand_pdbqt: str,
                result: DockingResult,
            ) -> None:
                nonlocal seed_completed
                nonlocal seed_dock_failed
                nonlocal seed_parse_failed
                nonlocal seed_non_interesting
                nonlocal seed_interesting
                nonlocal best_affinity

                record_dock_completion(result)
                if _dock_completed(result):
                    seed_completed += 1
                emit_progress("seed_dock")

                if not result.success or not result.pose_path:
                    seed_dock_failed += 1
                    _cleanup_pose_path(result.pose_path)
                    return

                pose_file = Path(result.pose_path)
                if not pose_file.exists():
                    seed_dock_failed += 1
                    _cleanup_pose_path(result.pose_path)
                    return

                try:
                    pose_text = pose_file.read_text(encoding="utf-8")
                    modes = parse_log(result.log_text)
                    atoms = parse_pose(pose_text)
                    if not modes or not atoms:
                        seed_parse_failed += 1
                        return

                    fingerprint = compute_fingerprint(
                        atoms,
                        protein_residues,
                        target_config.pocket,
                    )
                    observation = cov_map.observe(fingerprint)
                    affinity = modes[0].affinity

                    verdict = evaluate_and_process_hit(
                        smiles=smiles,
                        ligand_pdbqt=ligand_pdbqt,
                        modes=modes,
                        pose_text=pose_text,
                        initial_pose_path=result.pose_path,
                        initial_affinity=affinity,
                        oracle_stage="seed_oracle",
                        confirm_stage="seed_confirm",
                    )

                    if best_affinity is None or affinity < best_affinity:
                        best_affinity = affinity

                    seed_successful_candidates.append(
                        (
                            affinity,
                            smiles,
                            source_id,
                            observation.novelty_score,
                            1 if verdict.is_hit else 0,
                        )
                    )
                    if observation.novelty_score == 0 and not verdict.is_hit:
                        seed_non_interesting += 1
                        return

                    seed_entry = CorpusEntry(
                        smiles=smiles,
                        source_id=source_id,
                        priority=0.1,
                        best_affinity=affinity,
                        novelty_score=observation.novelty_score,
                        finds=1 if verdict.is_hit else 0,
                    )
                    seed_entry.priority = score_corpus_entry(
                        seed_entry,
                        priority_new_bit_weight=priority_new_bit_weight,
                        priority_affinity_weight=priority_affinity_weight,
                        priority_reuse_penalty=priority_reuse_penalty,
                    )
                    corpus.add(seed_entry)
                    seed_interesting += 1
                    interesting_smiles.add(smiles)
                finally:
                    _cleanup_pose_path(result.pose_path)

            def flush_seed_batch() -> None:
                nonlocal seed_attempted
                nonlocal current_stage
                nonlocal current_parent
                nonlocal current_smiles
                nonlocal current_mutation_stage
                nonlocal current_mutation_type

                if not seed_pending:
                    return

                batch = seed_pending.copy()
                seed_pending.clear()

                current_stage = "seed_dock"
                emit_progress()

                if pool is not None:
                    jobs = [
                        (idx, pdbqt, target_config, exhaustiveness, num_modes, engine)
                        for idx, (_smiles, _source_id, pdbqt) in enumerate(batch)
                    ]
                    record_dock_attempts(len(jobs))
                    seed_attempted += len(jobs)
                    results_by_idx: list[DockingResult | None] = [None] * len(batch)
                    try:
                        iterator = _pool_imap_unordered_single_chunk(pool, jobs)
                    except Exception as exc:
                        raise_manual_abort_on_pool_pipe_error(exc)
                        raise
                    pending = len(batch)
                    while pending > 0:
                        try:
                            idx, result = iterator.next(timeout=POOL_RESULT_POLL_TIMEOUT_SECONDS)
                        except PoolTimeoutError:
                            if abort_requested:
                                raise KeyboardInterrupt
                            emit_progress_heartbeat("seed_dock")
                            continue
                        except Exception as exc:
                            raise_manual_abort_on_pool_pipe_error(exc)
                            raise
                        results_by_idx[idx] = result
                        pending -= 1
                        smiles, source_id, _pdbqt = batch[idx]
                        current_parent = source_id
                        current_smiles = smiles
                        current_mutation_stage = "seed"
                        current_mutation_type = "seed"
                        process_seed_result(smiles, source_id, _pdbqt, result)
                    if any(result is None for result in results_by_idx):
                        raise RuntimeError("worker pool returned incomplete seed docking results")
                    return

                for smiles, source_id, pdbqt in batch:
                    current_parent = source_id
                    current_smiles = smiles
                    current_mutation_stage = "seed"
                    current_mutation_type = "seed"
                    record_dock_attempts()
                    seed_attempted += 1
                    result = dock(
                        pdbqt,
                        target_config,
                        exhaustiveness=exhaustiveness,
                        num_modes=num_modes,
                        engine=engine,
                    )
                    process_seed_result(smiles, source_id, pdbqt, result)

            log("[SEED] docking seeds to build initial corpus")
            for smiles, source_id in load_smiles(seed_smiles_path):
                if abort_requested:
                    raise KeyboardInterrupt
                seed_total += 1
                if smiles in seen_seed_smiles:
                    seed_duplicates_skipped += 1
                    continue
                seen_seed_smiles.add(smiles)
                current_stage = "seed_prepare"
                current_parent = source_id
                current_smiles = smiles
                current_mutation_stage = "seed"
                current_mutation_type = "seed"
                emit_progress()

                cached = cache.get(smiles)
                if cached is None:
                    pdbqt = prepare_smiles(
                        smiles,
                        min_mw=molecule_min_mw,
                        max_mw=molecule_max_mw,
                        max_logp=molecule_max_logp,
                        max_hbd=molecule_max_hbd,
                        max_hba=molecule_max_hba,
                        max_rot_bonds=molecule_max_rot_bonds,
                        allow_fallback=True,
                    )
                    if pdbqt is None:
                        pdbqt = prepare_smiles(
                            smiles,
                            require_drug_like=False,
                            min_mw=molecule_min_mw,
                            max_mw=molecule_max_mw,
                            max_logp=molecule_max_logp,
                            max_hbd=molecule_max_hbd,
                            max_hba=molecule_max_hba,
                            max_rot_bonds=molecule_max_rot_bonds,
                            allow_fallback=True,
                        )
                        if pdbqt is not None:
                            seed_prepare_relaxed += 1
                    if pdbqt is None:
                        seed_prepare_failed += 1
                        continue
                    cache.set(smiles, pdbqt)
                    cached = pdbqt

                seed_pending.append((smiles, source_id, cached))
                if len(seed_pending) >= seed_dock_batch_size:
                    flush_seed_batch()

            flush_seed_batch()

            if (
                seed_interesting < SEED_FALLBACK_MIN_INTERESTING
                and seed_successful_candidates
                and stopped_reason == "completed"
            ):
                top_k = min(SEED_FALLBACK_TOP_K, len(seed_successful_candidates))
                fallback_seen: set[str] = set()
                for (
                    affinity,
                    smiles,
                    source_id,
                    novelty_score,
                    finds_count,
                ) in sorted(
                    seed_successful_candidates,
                    key=lambda item: item[0],
                )[:top_k]:
                    if smiles in interesting_smiles or smiles in fallback_seen:
                        continue
                    fallback_seen.add(smiles)
                    fallback_entry = CorpusEntry(
                        smiles=smiles,
                        source_id=source_id,
                        priority=0.1,
                        best_affinity=affinity,
                        novelty_score=novelty_score,
                        finds=finds_count,
                    )
                    fallback_entry.priority = score_corpus_entry(
                        fallback_entry,
                        priority_new_bit_weight=priority_new_bit_weight,
                        priority_affinity_weight=priority_affinity_weight,
                        priority_reuse_penalty=priority_reuse_penalty,
                    )
                    corpus.add(fallback_entry)
                    seed_fallback_added += 1
                log(
                    "[SEED][FALLBACK] interesting={} (<{}) so top-affinity fallback retained {} seeds "
                    "(K={}) from successful docks"
                    .format(
                        seed_interesting,
                        SEED_FALLBACK_MIN_INTERESTING,
                        seed_fallback_added,
                        top_k,
                    )
                )

            log(
                "[SEED] completed seed docking: total={} attempted={} completed={} "
                "interesting={} fallback_added={} prepare_failed={} prepare_relaxed={} "
                "dock_failed={} parse_failed={} non_interesting={} duplicates_skipped={} corpus={}"
                .format(
                    seed_total,
                    seed_attempted,
                    seed_completed,
                    seed_interesting,
                    seed_fallback_added,
                    seed_prepare_failed,
                    seed_prepare_relaxed,
                    seed_dock_failed,
                    seed_parse_failed,
                    seed_non_interesting,
                    seed_duplicates_skipped,
                    corpus.size(),
                )
            )
        except KeyboardInterrupt:
            stopped_reason = "keyboard_interrupt"
            current_stage = "stopped"
            suppress_sigint()
            log("[STOP] manual quit requested (Ctrl-C); terminating workers and saving checkpoints")
            pool_needs_terminate = True
        except Exception as exc:
            stopped_reason = "failed"
            failure_reason = f"{type(exc).__name__}: {exc}"
            current_stage = "failed"
            log(f"[FAIL] campaign failed: {failure_reason}; terminating workers and saving checkpoints")
            pool_needs_terminate = True

    if corpus.size() == 0 and stopped_reason == "completed":
        stopped_reason = "empty_corpus"

    try:
        emit_progress()
        while stopped_reason == "completed":
            if abort_requested:
                raise KeyboardInterrupt
            if max_iterations is not None and iterations >= max_iterations:
                stopped_reason = "max_iterations"
                break
            if corpus.size() == 0:
                stopped_reason = "empty_corpus"
                break

            entry = corpus.pop()
            entry.times_mutated += 1
            entry.priority = score_corpus_entry(
                entry,
                priority_new_bit_weight=priority_new_bit_weight,
                priority_affinity_weight=priority_affinity_weight,
                priority_reuse_penalty=priority_reuse_penalty,
            )

            current_stage = "mutate"
            current_parent = entry.smiles
            current_smiles = None
            current_mutation_type = None
            current_power_score, current_budget = compute_mutation_budget(entry, mutations_per_entry)
            current_mutation_stage = "havoc"
            emit_progress()

            mutants = _mutate_candidates(
                entry.smiles,
                current_budget,
                molecule_min_mw,
                molecule_max_mw,
                molecule_max_logp,
                molecule_max_hbd,
                molecule_max_hba,
                molecule_max_rot_bonds,
            )
            if not mutants:
                corpus.add(entry)
                emit_progress("idle")
                continue

            prepared: list[tuple[MutationCandidate, str]] = []
            for candidate in mutants:
                current_stage = "prepare"
                current_smiles = candidate.smiles
                current_mutation_stage = candidate.stage
                current_mutation_type = candidate.mutation_type
                emit_progress()

                smiles = candidate.smiles
                cached = cache.get(smiles)
                if cached is None:
                    pdbqt = prepare_smiles(
                        smiles,
                        min_mw=molecule_min_mw,
                        max_mw=molecule_max_mw,
                        max_logp=molecule_max_logp,
                        max_hbd=molecule_max_hbd,
                        max_hba=molecule_max_hba,
                        max_rot_bonds=molecule_max_rot_bonds,
                    )
                    if pdbqt is None:
                        continue
                    cache.set(smiles, pdbqt)
                    cached = pdbqt
                prepared.append((candidate, cached))

            if not prepared:
                corpus.add(entry)
                emit_progress("idle")
                continue

            current_stage = "dock"
            emit_progress()

            if pool is not None:
                jobs = [
                    (idx, pdbqt, target_config, exhaustiveness, num_modes, engine)
                    for idx, (_candidate, pdbqt) in enumerate(prepared)
                ]
                record_dock_attempts(len(prepared))
                results_by_idx: list[DockingResult | None] = [None] * len(prepared)
                try:
                    iterator = _pool_imap_unordered_single_chunk(pool, jobs)
                except Exception as exc:
                    raise_manual_abort_on_pool_pipe_error(exc)
                    raise
                pending = len(prepared)
                while pending > 0:
                    try:
                        idx, result = iterator.next(timeout=POOL_RESULT_POLL_TIMEOUT_SECONDS)
                    except PoolTimeoutError:
                        if abort_requested:
                            raise KeyboardInterrupt
                        emit_progress_heartbeat("dock")
                        continue
                    except Exception as exc:
                        raise_manual_abort_on_pool_pipe_error(exc)
                        raise
                    results_by_idx[idx] = result
                    pending -= 1
                    record_dock_completion(result)
                    candidate = prepared[idx][0]
                    current_smiles = candidate.smiles
                    current_mutation_stage = candidate.stage
                    current_mutation_type = candidate.mutation_type
                    emit_progress("dock")
                if any(result is None for result in results_by_idx):
                    raise RuntimeError("worker pool returned incomplete docking results")
                results = [result for result in results_by_idx if result is not None]
            else:
                results = []
                for candidate, pdbqt in prepared:
                    record_dock_attempts()
                    result = dock(
                        pdbqt,
                        target_config,
                        exhaustiveness=exhaustiveness,
                        num_modes=num_modes,
                        engine=engine,
                    )
                    results.append(result)
                    record_dock_completion(result)
                    current_smiles = candidate.smiles
                    current_mutation_stage = candidate.stage
                    current_mutation_type = candidate.mutation_type
                    emit_progress("dock")

            stop_after_batch = False
            for (candidate, pdbqt), result in zip(prepared, results):
                smiles = candidate.smiles
                current_smiles = smiles
                current_mutation_stage = candidate.stage
                current_mutation_type = candidate.mutation_type

                if max_iterations is not None and iterations >= max_iterations:
                    _cleanup_pose_path(result.pose_path)
                    stop_after_batch = True
                    continue
                iterations += 1

                if not result.success or not result.pose_path:
                    continue

                pose_file = Path(result.pose_path)
                if not pose_file.exists():
                    continue

                try:
                    pose_text = pose_file.read_text(encoding="utf-8")
                    modes = parse_log(result.log_text)
                    atoms = parse_pose(pose_text)
                    if not modes or not atoms:
                        continue

                    fingerprint = compute_fingerprint(
                        atoms,
                        protein_residues,
                        target_config.pocket,
                    )
                    observation = cov_map.observe(fingerprint)
                    affinity = modes[0].affinity

                    verdict = evaluate_and_process_hit(
                        smiles=smiles,
                        ligand_pdbqt=pdbqt,
                        modes=modes,
                        pose_text=pose_text,
                        initial_pose_path=result.pose_path,
                        initial_affinity=affinity,
                    )

                    child_entry = CorpusEntry(
                        smiles=smiles,
                        source_id=f"mutant_of:{entry.source_id}",
                        priority=0.1,
                        best_affinity=affinity,
                        novelty_score=observation.novelty_score,
                        finds=1 if verdict.is_hit else 0,
                    )
                    child_entry.priority = score_corpus_entry(
                        child_entry,
                        priority_new_bit_weight=priority_new_bit_weight,
                        priority_affinity_weight=priority_affinity_weight,
                        priority_reuse_penalty=priority_reuse_penalty,
                    )
                    corpus.add(child_entry)

                    if best_affinity is None or affinity < best_affinity:
                        best_affinity = affinity

                    if checkpoint_every > 0 and iterations % checkpoint_every == 0:
                        cov_map.save(coverage_checkpoint)
                        _save_corpus_checkpoints(corpus, corpus_checkpoint_paths)
                        checkpoint_count += 1
                        current_stage = "checkpoint"
                        emit_progress()
                finally:
                    _cleanup_pose_path(result.pose_path)

            corpus.add(entry)

            if stop_after_batch:
                stopped_reason = "max_iterations"
                break

            if checkpoint_every > 0 and iterations % checkpoint_every == 0:
                log(
                    "[CHKPT] iterations={} occupancy={:.3f} epoch={} corpus={} "
                    "hits={} novelty_strong={} novelty_weak={} novelty_none={}"
                    .format(
                        iterations,
                        cov_map.bitmap_occupancy(),
                        cov_map.epoch,
                        corpus.size(),
                        hits,
                        cov_map.strong_novelty_count,
                        cov_map.weak_novelty_count,
                        cov_map.none_novelty_count,
                    )
                )
            current_stage = "idle"
            emit_progress()

    except KeyboardInterrupt:
        stopped_reason = "keyboard_interrupt"
        current_stage = "stopped"
        suppress_sigint()
        log("[STOP] manual quit requested (Ctrl-C); terminating workers and saving checkpoints")
        pool_needs_terminate = True
    except Exception as exc:
        stopped_reason = "failed"
        failure_reason = f"{type(exc).__name__}: {exc}"
        current_stage = "failed"
        log(f"[FAIL] campaign failed: {failure_reason}; terminating workers and saving checkpoints")
        pool_needs_terminate = True

    try:
        if pool is not None:
            try:
                try:
                    if pool_needs_terminate:
                        pool.terminate()
                    else:
                        pool.close()
                except Exception as exc:
                    if not (stopped_reason == "keyboard_interrupt" and is_pool_pipe_error(exc)):
                        log(f"[WARN] worker pool shutdown step failed: {type(exc).__name__}: {exc}")
            finally:
                try:
                    if stopped_reason == "keyboard_interrupt":
                        join_done, join_errors = _start_pool_join_thread(pool)
                        if not join_done.wait(POOL_ABORT_JOIN_TIMEOUT_SECONDS):
                            killed_workers = _force_kill_pool_workers(pool)
                            log(
                                "[WARN] worker pool join timed out after {:.1f}s during manual abort; "
                                "forced kill sent to {} worker(s)"
                                .format(POOL_ABORT_JOIN_TIMEOUT_SECONDS, killed_workers)
                            )
                            join_done.wait(POOL_ABORT_JOIN_GRACE_SECONDS)
                        if join_done.is_set():
                            if join_errors:
                                raise join_errors[0]
                        else:
                            log(
                                "[WARN] worker pool join still pending after forced kill; "
                                "continuing shutdown without blocking"
                            )
                    else:
                        pool.join()
                except Exception as exc:
                    if not (stopped_reason == "keyboard_interrupt" and is_pool_pipe_error(exc)):
                        log(f"[WARN] worker pool join failed: {type(exc).__name__}: {exc}")
        try:
            cov_map.save(coverage_checkpoint)
            _save_corpus_checkpoints(corpus, corpus_checkpoint_paths)
            if iterations > 0 or corpus.size() > 0:
                checkpoint_count += 1
        except Exception as exc:
            if stopped_reason == "keyboard_interrupt":
                log(
                    "[WARN] failed to save final checkpoints after manual abort: "
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                stopped_reason = "failed"
                failure_reason = f"{type(exc).__name__}: {exc}"
                current_stage = "failed"
                log(f"[FAIL] failed to save final checkpoints: {failure_reason}")
    finally:
        if sigint_guard_installed:
            try:
                signal.signal(signal.SIGINT, previous_sigint_handler)
            except ValueError:
                pass

    if stopped_reason == "failed":
        current_stage = "failed"
    elif stopped_reason == "keyboard_interrupt":
        current_stage = "stopped"
    else:
        current_stage = "finished"
    emit_progress()

    stats: dict[str, float | int | str] = {
        "iterations": iterations,
        "hits": hits,
        "corpus_size": corpus.size(),
        "best_affinity": best_affinity if best_affinity is not None else 0.0,
        # Kept for CLI/backward compatibility; mirrors attempted dock calls.
        "total_docks": attempted_docks,
        "attempted_docks": attempted_docks,
        "completed_docks": completed_docks,
        "stopped_reason": stopped_reason,
    }
    stats.update(cov_map.stats())
    stats.update(
        {
            "selectivity_passed_count": selectivity_counts["passed"],
            "selectivity_failed_count": selectivity_counts["failed"],
            "selectivity_skipped_unavailable_count": selectivity_counts[
                "skipped_unavailable"
            ],
            "selectivity_not_configured_count": selectivity_counts[
                "not_configured"
            ],
        }
    )
    if failure_reason is not None:
        stats["failure_reason"] = failure_reason
    return stats
