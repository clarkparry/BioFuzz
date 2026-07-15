from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from biofuzz.corpus import Corpus, CorpusEntry
from biofuzz.corpus import mutation_budget as compute_mutation_budget
from biofuzz.coverage import CoverageMap
from biofuzz.docker import DockingConfig
from biofuzz.docker.gnina import GninaBackend
from biofuzz.docker.parser import parse_log, parse_pose
from biofuzz.fuzzer.status import RuntimeStatus
from biofuzz.fuzzer.worker import dock_worker
from biofuzz.mutator import mutate_with_metadata
from biofuzz.oracle import OracleConfig, evaluate
from biofuzz.prep import prepare_smiles
from biofuzz.protein import parse_receptor_residues
from biofuzz.seeds import compute_seed_priority, load_seeds
from biofuzz.storage import FindingsStore, FuzzerLog, PDBQTCache, make_run_dir


@dataclass
class CampaignState:
    corpus: Corpus
    coverage: CoverageMap
    findings: FindingsStore
    cache: PDBQTCache
    iterations: int = 0
    hits: int = 0
    best_affinity: float | None = None
    checkpoint_count: int = 0
    stopped_reason: str = ""


class Campaign:
    def __init__(
        self,
        target_name: str,
        target_config: dict,
        global_config: dict,
        output_dir: str | Path,
        seeds_path: str | None = None,
        workers: int = 1,
        max_iterations: int | None = None,
        checkpoint_every: int = 500,
        ui_callback=None,
        ui=None,
    ):
        self.target_name = target_name
        self.target_config = target_config
        self.global_config = global_config
        self.workers = workers
        self.max_iterations = max_iterations
        self.checkpoint_every = checkpoint_every
        self.ui_callback = ui_callback
        # `ui` is an optional richer object (e.g. FuzzerTUI) exposing both
        # .update(status) and .log(message) -- the fuzzer.md doc only
        # specifies a bare status callback, but ui.md's own log()/notice()
        # methods need something to call them; this is that something.
        self.ui = ui

        self.layout = make_run_dir(output_dir, target_name)
        self.log = FuzzerLog(self.layout.log_path)

        molecules_cfg = global_config.get("molecules", {})
        self.filter_kwargs = dict(
            min_mw=molecules_cfg.get("min_mw", 0.0),
            max_mw=molecules_cfg.get("max_mw", 550.0),
            max_logp=molecules_cfg.get("max_logp", 5.0),
            max_hbd=molecules_cfg.get("max_hbd", 10),
            max_hba=molecules_cfg.get("max_hba", 10),
            max_rot_bonds=molecules_cfg.get("max_rot_bonds", 10),
        )
        self.base_mutations = molecules_cfg.get("mutations_per_entry", 20)

        corpus_cfg = global_config.get("corpus", {})
        self.state = CampaignState(
            corpus=Corpus(
                max_size=corpus_cfg.get("max_size", 50000),
                novelty_weight=corpus_cfg.get("priority_new_bit_weight", 1.0),
                affinity_weight=corpus_cfg.get("priority_affinity_weight", 1.0),
                base_mutations=self.base_mutations,
            ),
            coverage=self._build_coverage(target_config, global_config),
            findings=FindingsStore(self.layout.findings_dir),
            cache=PDBQTCache(self.layout.cache_dir),
        )

        self.receptor_path = str(Path("targets") / target_name / target_config["receptor"])
        pocket_residue_ids = set(target_config["pocket"]["residue_ids"])
        self.protein_residues = parse_receptor_residues(self.receptor_path, pocket_residue_ids)

        self.docking_config = self._build_docking_config(target_config, global_config)
        self.oracle_config = self._build_oracle_config(target_config)

        self._start_time = time.time()
        self._total_docks = 0
        self._completed_docks = 0
        self._last_gpu_active = None

        if seeds_path:
            self._load_seeds(seeds_path)

    def _build_coverage(self, target_config: dict, global_config: dict) -> CoverageMap:
        pocket_residue_ids = set(target_config["pocket"]["residue_ids"])
        coverage_cfg = global_config.get("coverage", {})
        return CoverageMap(
            pocket_residue_ids=pocket_residue_ids,
            map_size_bytes=coverage_cfg.get("map_size_kib", 256) * 1024,
            occupancy_rotate_threshold=coverage_cfg.get("occupancy_rotate_threshold", 0.55),
            interaction_types_enabled=coverage_cfg.get("interaction_types", False),
            contact_cutoff=target_config["pocket"].get("contact_cutoff", 3.5),
        )

    def _build_docking_config(self, target_config: dict, global_config: dict) -> DockingConfig:
        docking_cfg = global_config.get("docking", {})
        box = target_config["box"]
        return DockingConfig(
            receptor_path=self.receptor_path,
            center_x=box["center_x"], center_y=box["center_y"], center_z=box["center_z"],
            size_x=box["size_x"], size_y=box["size_y"], size_z=box["size_z"],
            exhaustiveness=docking_cfg.get("exhaustiveness_fuzz", 4),
            num_modes=docking_cfg.get("num_modes", 3),
            timeout_seconds=docking_cfg.get("timeout_seconds", 120),
            workers=self.workers,
            cnn_model=docking_cfg.get("cnn_model_fuzz", "fast"),
        )

    def _build_oracle_config(self, target_config: dict) -> OracleConfig:
        oracle_cfg = target_config.get("oracle", {})
        return OracleConfig(
            affinity_threshold=oracle_cfg.get("affinity_threshold", -9.0),
            strain_threshold=oracle_cfg.get("strain_threshold", 3.5),
        )

    def _load_seeds(self, seeds_path: str) -> None:
        for smiles, seed_id in load_seeds(seeds_path):
            priority = compute_seed_priority(smiles, target_specific=False)
            self.state.corpus.add(CorpusEntry(smiles=smiles, source_id=seed_id, priority=priority))

        per_target_dir = Path("seeds") / "per_target" / self.target_name
        if per_target_dir.is_dir():
            for seed_file in per_target_dir.glob("*.smi"):
                for smiles, seed_id in load_seeds(seed_file):
                    priority = compute_seed_priority(smiles, target_specific=True)
                    self.state.corpus.add(
                        CorpusEntry(smiles=smiles, source_id=seed_id, priority=priority)
                    )

        self.log.write("INFO", f"[SEED] adding {self.state.corpus.size()} seeds to corpus")

    def _emit_status(self, stage: str, **kwargs) -> None:
        if self.ui_callback is None and self.ui is None:
            return
        elapsed = time.time() - self._start_time
        docks_per_sec = self._completed_docks / elapsed if elapsed > 0 else 0.0
        status = RuntimeStatus(
            stage=stage,
            mutation_stage=kwargs.get("mutation_stage", ""),
            mutation_type=kwargs.get("mutation_type"),
            current_parent=kwargs.get("current_parent"),
            current_smiles=kwargs.get("current_smiles"),
            power_score=kwargs.get("power_score", 0.0),
            mutation_budget=kwargs.get("mutation_budget", 0),
            total_docks=self._total_docks,
            completed_docks=self._completed_docks,
            docks_per_sec=docks_per_sec,
            corpus_size=self.state.corpus.size(),
            hits=self.state.hits,
            coverage_bitmap_occupancy=self.state.coverage.bitmap_occupancy(),
            coverage_epoch=self.state.coverage.epoch,
            novelty_strong=self.state.coverage.novelty_counts["strong"],
            novelty_weak=self.state.coverage.novelty_counts["weak"],
            novelty_none=self.state.coverage.novelty_counts["none"],
            best_affinity=self.state.best_affinity,
            checkpoints=self.state.checkpoint_count,
            elapsed_seconds=elapsed,
            gpu_active=self._last_gpu_active,
        )
        if self.ui_callback is not None:
            self.ui_callback(status)
        if self.ui is not None:
            self.ui.update(status)

    def _select_stage(self, entry: CorpusEntry) -> tuple[str, str | None]:
        # Corpus.pop() increments times_selected before returning the entry,
        # so a value of 1 here means this is the first time it's been popped.
        if entry.times_selected <= 1:
            return "deterministic", None
        donor = self.state.corpus.sample_donor(exclude_smiles=entry.smiles)
        if donor is not None:
            return "splice", donor.smiles
        return "havoc", None

    def _prepare_mutants(self, mutants) -> list[tuple[object, str]]:
        prepared = []
        for mutant in mutants:
            pdbqt = self.state.cache.get(mutant.smiles)
            if pdbqt is None:
                pdbqt = prepare_smiles(mutant.smiles, **self.filter_kwargs)
                if pdbqt is None:
                    continue
                self.state.cache.set(mutant.smiles, pdbqt)
            prepared.append((mutant, pdbqt))
        return prepared

    def _dock_all(self, prepared: list[tuple[object, str]]) -> list[tuple[object, object]]:
        results = []
        self._total_docks += len(prepared)

        if self.workers > 1 and len(prepared) > 1:
            with ProcessPoolExecutor(max_workers=self.workers) as pool:
                futures = {
                    pool.submit(dock_worker, pdbqt, self.docking_config): mutant
                    for mutant, pdbqt in prepared
                }
                try:
                    for future in as_completed(futures):
                        mutant = futures[future]
                        result = future.result()
                        results.append((mutant, result))
                        self._completed_docks += 1
                        if result.gpu_active is not None:
                            self._last_gpu_active = result.gpu_active
                except KeyboardInterrupt:
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise
        else:
            backend = GninaBackend()
            for mutant, pdbqt in prepared:
                result = backend.dock(pdbqt, self.docking_config)
                results.append((mutant, result))
                self._completed_docks += 1
                if result.gpu_active is not None:
                    self._last_gpu_active = result.gpu_active

        return results

    def _process_results(self, entry: CorpusEntry, results) -> None:
        for mutant, result in results:
            if not result.success:
                continue
            try:
                pose_text = open(result.pose_path).read()
                pose_atoms = parse_pose(pose_text)
                obs = self.state.coverage.observe(
                    pose_atoms, self.protein_residues, smiles=mutant.smiles
                )
                modes = parse_log(result.log_text)
                verdict = evaluate(modes, pose_text, self.oracle_config)

                if verdict.is_hit:
                    self.state.findings.save(
                        smiles=mutant.smiles,
                        verdict={"passed_tiers": verdict.passed_tiers, "notes": verdict.notes},
                        pose_path=result.pose_path,
                        affinity=verdict.affinity,
                        strain=verdict.strain,
                        mutation_stage=mutant.stage,
                        mutation_type=mutant.mutation_type,
                        parent_smiles=mutant.parent_smiles,
                        corpus_source_id=entry.source_id,
                    )
                    self.state.hits += 1
                    hit_message = f"[HIT] {mutant.smiles} | affinity={verdict.affinity:.2f}"
                    self.log.write("HIT", hit_message)
                    if self.ui is not None:
                        self.ui.log(hit_message)

                if self.state.best_affinity is None or verdict.affinity < self.state.best_affinity:
                    self.state.best_affinity = verdict.affinity

                self.state.corpus.add(
                    mutant.smiles,
                    novelty=obs.novelty_score,
                    affinity=verdict.affinity,
                    mutation_type=mutant.mutation_type,
                )
            finally:
                if result.pose_path and os.path.exists(result.pose_path):
                    os.unlink(result.pose_path)

    def run_iteration(self) -> bool:
        entry = self.state.corpus.pop()
        if entry is None:
            return False

        stage, donor_smiles = self._select_stage(entry)
        budget = compute_mutation_budget(entry, self.base_mutations)

        self._emit_status(
            "mutate", mutation_stage=stage, current_parent=entry.smiles, mutation_budget=budget
        )

        mutants = mutate_with_metadata(
            entry.smiles, n=budget, stage=stage, donor_smiles=donor_smiles, **self.filter_kwargs
        )

        prepared = self._prepare_mutants(mutants)

        self._emit_status("dock", mutation_stage=stage)
        results = self._dock_all(prepared)

        self._process_results(entry, results)

        entry.times_fuzzed += 1
        entry.priority = self.state.corpus.compute_priority(entry)
        self.state.corpus.add(entry)

        self.state.iterations += 1

        if self.state.iterations % self.checkpoint_every == 0:
            self.checkpoint()

        self._emit_status("idle", mutation_stage=stage)
        return True

    def checkpoint(self) -> None:
        self.state.corpus.save(self.layout.corpus_dir / "state.json")
        self.state.coverage.save(self.layout.coverage_path)
        self.state.checkpoint_count += 1
        self.log.write(
            "CHKPT",
            f"iterations={self.state.iterations} "
            f"occupancy={self.state.coverage.bitmap_occupancy():.3f} "
            f"epoch={self.state.coverage.epoch} corpus={self.state.corpus.size()}",
        )

    def run(self) -> CampaignState:
        try:
            while self.max_iterations is None or self.state.iterations < self.max_iterations:
                if not self.run_iteration():
                    break
            else:
                self.state.stopped_reason = "max_iterations"
            if not self.state.stopped_reason:
                self.state.stopped_reason = "corpus_exhausted"
        except KeyboardInterrupt:
            self.state.stopped_reason = "keyboard_interrupt"
            self.checkpoint()
            self.log.write(
                "INFO",
                f"campaign stopped: {self.state.stopped_reason} "
                f"iterations={self.state.iterations} hits={self.state.hits}",
            )
            self.log.close()
            if self.ui is not None:
                self.ui.close()
            raise

        self.checkpoint()
        self.log.write(
            "INFO",
            f"campaign stopped: {self.state.stopped_reason} "
            f"iterations={self.state.iterations} hits={self.state.hits}",
        )
        self.log.close()
        if self.ui is not None:
            self.ui.close()
        return self.state
