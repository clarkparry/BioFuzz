from __future__ import annotations

from multiprocessing import Pool
from pathlib import Path
import json
from typing import Callable

from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.coverage import CoverageMap
from biofuzz.docking.config import TargetConfig
from biofuzz.docking.parser import parse_log, parse_pose
from biofuzz.docking.runner import DockingResult, dock
from biofuzz.molecules.mutator import mutate
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


def _dock_worker(payload: tuple[str, TargetConfig, int, int, str]) -> DockingResult:
    pdbqt, target_config, exhaustiveness, num_modes, engine = payload
    return dock(
        pdbqt,
        target_config,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        engine=engine,
    )


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
) -> dict[str, float | int]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        if logger:
            logger(message)

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

    if corpus.size() == 0:
        for smiles, source_id in load_smiles(seed_smiles_path):
            corpus.add(
                CorpusEntry(
                    smiles=smiles,
                    source_id=source_id,
                    priority=1.0,
                )
            )

    if corpus.size() == 0:
        return {
            "iterations": 0,
            "hits": 0,
            "coverage_ratio": cov_map.coverage_ratio(),
            "corpus_size": 0,
        }

    protein_residues = {}
    receptor_path = Path(target_config.receptor)
    if receptor_path.exists():
        protein_residues = load_residue_coordinates(receptor_path)
    else:
        log(f"[WARN] receptor not found for coverage parsing: {receptor_path}")

    iterations = 0
    hits = 0
    best_affinity: float | None = None
    selectivity_exhaustiveness = max(
        8,
        exhaustiveness_confirm if exhaustiveness_confirm is not None else exhaustiveness,
    )

    pool = Pool(processes=workers) if workers > 1 else None

    try:
        while corpus.size() > 0:
            if max_iterations is not None and iterations >= max_iterations:
                break

            entry = corpus.pop()
            entry.times_mutated += 1

            mutants = mutate(
                entry.smiles,
                n=mutations_per_entry,
                min_mw=molecule_min_mw,
                max_mw=molecule_max_mw,
                max_logp=molecule_max_logp,
                max_hbd=molecule_max_hbd,
                max_hba=molecule_max_hba,
                max_rot_bonds=molecule_max_rot_bonds,
            )
            if not mutants:
                continue

            prepared: list[tuple[str, str]] = []
            for smiles in mutants:
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
                prepared.append((smiles, cached))

            if not prepared:
                continue

            if pool is not None:
                jobs = [
                    (pdbqt, target_config, exhaustiveness, num_modes, engine)
                    for _smiles, pdbqt in prepared
                ]
                results = pool.map(_dock_worker, jobs)
            else:
                results = [
                    dock(
                        pdbqt,
                        target_config,
                        exhaustiveness=exhaustiveness,
                        num_modes=num_modes,
                        engine=engine,
                    )
                    for _smiles, pdbqt in prepared
                ]

            stop_after_batch = False
            for (smiles, pdbqt), result in zip(prepared, results):
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

                    verdict = evaluate(
                        modes,
                        pose_text,
                        target_config,
                        smiles=smiles,
                        ligand_pdbqt=pdbqt,
                        selectivity_exhaustiveness=selectivity_exhaustiveness,
                        num_modes=num_modes,
                        docking_engine=engine,
                    )
                    if verdict.is_hit:
                        confirmed_verdict = verdict
                        confirmed_affinity = affinity
                        confirmed_pose_path = result.pose_path

                        if exhaustiveness_confirm is not None and exhaustiveness_confirm > 0:
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

                    priority = compute_priority_weighted(
                        new_bits=len(new_bits),
                        affinity=affinity,
                        times_mutated=entry.times_mutated,
                        priority_new_bit_weight=priority_new_bit_weight,
                        priority_affinity_weight=priority_affinity_weight,
                        priority_reuse_penalty=priority_reuse_penalty,
                    )
                    corpus.add(
                        CorpusEntry(
                            smiles=smiles,
                            source_id=f"mutant_of:{entry.source_id}",
                            priority=priority,
                            best_affinity=affinity,
                            new_bits=len(new_bits),
                        )
                    )

                    if best_affinity is None or affinity < best_affinity:
                        best_affinity = affinity

                    if checkpoint_every > 0 and iterations % checkpoint_every == 0:
                        cov_map.save(coverage_checkpoint)
                        _save_corpus_checkpoints(corpus, corpus_checkpoint_paths)
                finally:
                    _cleanup_pose_path(result.pose_path)

            if stop_after_batch:
                break

            if checkpoint_every > 0 and iterations % checkpoint_every == 0:
                log(
                    "[CHKPT] iterations={} coverage={:.3f} corpus={} hits={}"
                    .format(iterations, cov_map.coverage_ratio(), corpus.size(), hits)
                )

    finally:
        if pool is not None:
            pool.close()
            pool.join()

    cov_map.save(coverage_checkpoint)
    _save_corpus_checkpoints(corpus, corpus_checkpoint_paths)

    return {
        "iterations": iterations,
        "hits": hits,
        "coverage_ratio": cov_map.coverage_ratio(),
        "corpus_size": corpus.size(),
        "best_affinity": best_affinity if best_affinity is not None else 0.0,
    }
