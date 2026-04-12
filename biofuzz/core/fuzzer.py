from __future__ import annotations

from multiprocessing import Pool, TimeoutError as PoolTimeoutError
from pathlib import Path
import json
import errno
import signal
import time
from typing import Callable

from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.coverage import CoverageMap
from biofuzz.core.tui import RuntimeStatus
from biofuzz.docking.config import TargetConfig
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
) -> float:
    return (
        (new_bits * priority_new_bit_weight)
        + (max(0.0, -affinity - 5.0) * priority_affinity_weight)
        - (times_mutated * priority_reuse_penalty)
    )


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
        )
        + find_bonus,
    )


def compute_power_score(entry: CorpusEntry) -> float:
    affinity = entry.best_affinity if entry.best_affinity is not None else -5.0
    affinity_bonus = max(0.0, -affinity - 5.0)
    return max(
        1.0,
        1.0
        + (entry.new_bits * 2.0)
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
        )
    return idx, result


def _cleanup_pose_path(pose_path: str | None) -> None:
    if pose_path:
        Path(pose_path).unlink(missing_ok=True)


def _load_saved_pocket_residue_ids(path: Path) -> set[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {int(value) for value in payload.get("pocket_residue_ids", [])}


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
    logger: Callable[[str], None] | None = None,
    progress_callback: Callable[[RuntimeStatus], None] | None = None,
) -> dict[str, float | int | str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

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
    total_docks = 0

    def emit_progress(force_stage: str | None = None) -> None:
        if progress_callback is None:
            return
        progress_callback(
            RuntimeStatus(
                stage=force_stage or current_stage,
                mutation_stage=current_mutation_stage,
                mutation_type=current_mutation_type,
                current_parent=current_parent,
                current_smiles=current_smiles,
                power_score=current_power_score,
                mutation_budget=current_budget,
                total_docks=total_docks,
                docks_per_sec=(total_docks / elapsed_seconds()) if total_docks else 0.0,
                corpus_size=corpus.size(),
                finds=hits,
                coverage_ratio=cov_map.coverage_ratio(),
                best_affinity=best_affinity,
                checkpoints=checkpoint_count,
                elapsed_seconds=elapsed_seconds(),
            )
        )

    def observe_extra_dock() -> None:
        nonlocal total_docks
        total_docks += 1
        emit_progress()

    corpus = Corpus(max_size=max_corpus_size)
    cov_map = CoverageMap(target_config.pocket.residue_ids)
    findings = FindingsStore(output / "findings")
    cache = PDBQTCache(output / "cache")
    coverage_checkpoint = output / "coverage.json"
    corpus_checkpoint_paths = _corpus_checkpoint_paths(output)

    if coverage_checkpoint.exists():
        try:
            saved_pocket_ids = _load_saved_pocket_residue_ids(coverage_checkpoint)
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
    selectivity_exhaustiveness = max(
        8,
        exhaustiveness_confirm if exhaustiveness_confirm is not None else exhaustiveness,
    )
    pool = Pool(processes=workers) if workers > 1 else None
    pool_needs_terminate = False
    previous_sigint_handler: int | Callable[[int, object], object] | None = None
    sigint_suppressed = False

    def suppress_sigint() -> None:
        nonlocal previous_sigint_handler, sigint_suppressed
        if sigint_suppressed:
            return
        try:
            previous_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            sigint_suppressed = True
        except ValueError:
            sigint_suppressed = False

    if corpus.size() == 0:
        try:
            seed_total = 0
            seed_interesting = 0
            log("[SEED] docking seeds to build initial corpus")
            for smiles, source_id in load_smiles(seed_smiles_path):
                seed_total += 1
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
                    )
                    if pdbqt is None:
                        continue
                    cache.set(smiles, pdbqt)
                    cached = pdbqt

                current_stage = "seed_dock"
                emit_progress()
                result = dock(
                    cached,
                    target_config,
                    exhaustiveness=exhaustiveness,
                    num_modes=num_modes,
                    engine=engine,
                )
                total_docks += 1
                emit_progress("seed_dock")

                if not result.success or not result.pose_path:
                    _cleanup_pose_path(result.pose_path)
                    continue

                pose_file = Path(result.pose_path)
                if not pose_file.exists():
                    _cleanup_pose_path(result.pose_path)
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
                    new_bits = cov_map.update(fingerprint)
                    affinity = modes[0].affinity

                    current_stage = "seed_oracle"
                    emit_progress()
                    verdict = evaluate(modes, pose_text, target_config.oracle)

                    if best_affinity is None or affinity < best_affinity:
                        best_affinity = affinity

                    if not new_bits and not verdict.is_hit:
                        continue

                    seed_entry = CorpusEntry(
                        smiles=smiles,
                        source_id=source_id,
                        priority=0.1,
                        best_affinity=affinity,
                        new_bits=len(new_bits),
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
                finally:
                    _cleanup_pose_path(result.pose_path)

            log(
                "[SEED] completed seed docking: total={} interesting={} corpus={}"
                .format(seed_total, seed_interesting, corpus.size())
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
                results_by_idx: list[DockingResult | None] = [None] * len(prepared)
                iterator = pool.imap_unordered(_dock_worker, jobs)
                pending = len(prepared)
                while pending > 0:
                    try:
                        idx, result = iterator.next(timeout=0.2)
                    except PoolTimeoutError:
                        continue
                    except (BrokenPipeError, EOFError) as exc:
                        log(
                            "[WARN] pool result channel closed during docking collection; "
                            "treating as manual abort"
                        )
                        raise KeyboardInterrupt from exc
                    except OSError as exc:
                        if getattr(exc, "errno", None) != errno.EPIPE:
                            raise
                        log(
                            "[WARN] pool result channel closed during docking collection; "
                            "treating as manual abort"
                        )
                        raise KeyboardInterrupt from exc
                    results_by_idx[idx] = result
                    pending -= 1
                    total_docks += 1
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
                    result = dock(
                        pdbqt,
                        target_config,
                        exhaustiveness=exhaustiveness,
                        num_modes=num_modes,
                        engine=engine,
                    )
                    results.append(result)
                    total_docks += 1
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
                    new_bits = cov_map.update(fingerprint)
                    affinity = modes[0].affinity

                    current_stage = "oracle"
                    emit_progress()

                    verdict = evaluate(
                        modes,
                        pose_text,
                        target_config,
                        smiles=smiles,
                        ligand_pdbqt=pdbqt,
                        selectivity_exhaustiveness=selectivity_exhaustiveness,
                        num_modes=num_modes,
                        docking_engine=engine,
                        dock_observer=observe_extra_dock,
                    )
                    if verdict.is_hit:
                        confirmed_verdict = verdict
                        confirmed_affinity = affinity
                        confirmed_pose_path = result.pose_path

                        if exhaustiveness_confirm is not None and exhaustiveness_confirm > 0:
                            total_docks += 1
                            current_stage = "confirm"
                            emit_progress()
                            confirm_result = dock(
                                pdbqt,
                                target_config,
                                exhaustiveness=exhaustiveness_confirm,
                                num_modes=num_modes,
                                engine=engine,
                            )
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
                                                ligand_pdbqt=pdbqt,
                                                selectivity_exhaustiveness=selectivity_exhaustiveness,
                                                num_modes=num_modes,
                                                docking_engine=engine,
                                                dock_observer=observe_extra_dock,
                                            )
                                            confirmed_pose_path = confirm_result.pose_path
                            finally:
                                if confirmed_pose_path != confirm_result.pose_path:
                                    _cleanup_pose_path(confirm_result.pose_path)

                        if confirmed_verdict.is_hit and confirmed_pose_path:
                            findings.save(smiles, confirmed_verdict, confirmed_pose_path)
                            hits += 1
                            log(
                                "[HIT] {} | affinity={:.2f} | confirmed_exhaustiveness={}"
                                .format(smiles, confirmed_affinity, exhaustiveness_confirm)
                            )
                            if confirmed_pose_path != result.pose_path:
                                _cleanup_pose_path(confirmed_pose_path)
                        elif confirmed_pose_path and confirmed_pose_path != result.pose_path:
                            _cleanup_pose_path(confirmed_pose_path)

                    child_entry = CorpusEntry(
                        smiles=smiles,
                        source_id=f"mutant_of:{entry.source_id}",
                        priority=0.1,
                        best_affinity=affinity,
                        new_bits=len(new_bits),
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
                    "[CHKPT] iterations={} coverage={:.3f} corpus={} hits={}"
                    .format(iterations, cov_map.coverage_ratio(), corpus.size(), hits)
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
                    log(f"[WARN] worker pool shutdown step failed: {type(exc).__name__}: {exc}")
            finally:
                try:
                    pool.join()
                except Exception as exc:
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
        if sigint_suppressed:
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
        "coverage_ratio": cov_map.coverage_ratio(),
        "corpus_size": corpus.size(),
        "best_affinity": best_affinity if best_affinity is not None else 0.0,
        "total_docks": total_docks,
        "stopped_reason": stopped_reason,
    }
    if failure_reason is not None:
        stats["failure_reason"] = failure_reason
    return stats
