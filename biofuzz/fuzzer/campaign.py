from __future__ import annotations

import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from biofuzz.corpus import Corpus, CorpusEntry
from biofuzz.corpus import mutation_budget as compute_mutation_budget
from biofuzz.corpus import select_stage
from biofuzz.coverage import CoverageMap
from biofuzz.coverage.fingerprint import build_fingerprint
from biofuzz.docker import DockingConfig, get_backend
from biofuzz.docker.parser import parse_log, parse_pose
from biofuzz.fuzzer.status import RuntimeStatus
from biofuzz.fuzzer.worker import dock_worker, prepare_worker
from biofuzz.mutator import mutate_with_metadata
from biofuzz.mutator.entry import MutationCandidate
from biofuzz.oracle import OracleConfig, evaluate
from biofuzz.prep import prepare_smiles
from biofuzz.protein import (
    EssentialResidues,
    parse_receptor_residues,
    select_static_essential,
)
from biofuzz.seeds import compute_seed_priority, load_seeds
from biofuzz.storage import (
    CheckpointVersionError,
    FindingsStore,
    FuzzerLog,
    PDBQTCache,
    load_checkpoint,
    make_run_dir,
    open_run_dir,
    save_checkpoint,
)


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
    # Canonical SMILES already docked in this campaign. Docking is the entire
    # cost of the loop, so re-docking a molecule a mutation happened to
    # rediscover is pure waste.
    evaluated: set = field(default_factory=set)
    docks_skipped: int = 0
    prep_failures: int = 0


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
        resume_dir: str | Path | None = None,
        checkpoint_every_seconds: float | None = 300.0,
        seed: int | None = None,
    ):
        self.target_name = target_name
        self.target_config = target_config
        self.global_config = global_config
        self.workers = workers
        self.max_iterations = max_iterations
        self.checkpoint_every = checkpoint_every
        self.checkpoint_every_seconds = checkpoint_every_seconds
        self.ui_callback = ui_callback
        # Two display channels, because one is not enough. `ui_callback` is the
        # minimal contract: status structs only. `ui` is a richer object (e.g.
        # FuzzerTUI) exposing .update(status), .log(message) and .close(), since
        # hit text and notable events cannot travel through a status struct.
        # Either, both, or neither may be attached.
        self.ui = ui
        self._rng = random.Random(seed)

        resuming = resume_dir is not None
        self.layout = (
            open_run_dir(resume_dir) if resuming else make_run_dir(output_dir, target_name)
        )
        self.log = FuzzerLog(self.layout.log_path)

        # Per-target first (merge_defaults overlays it onto the global section):
        # viable chemical space differs by target, and a global-only bound
        # silently filters out whole drug classes -- see merge_defaults.
        molecules_cfg = target_config.get("molecules") or global_config.get("molecules", {})
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
                scaffold_penalty_weight=corpus_cfg.get("priority_scaffold_penalty", 3.0),
            ),
            coverage=self._build_coverage(target_config, global_config),
            findings=FindingsStore(self.layout.findings_dir),
            cache=PDBQTCache(self.layout.cache_dir),
        )

        self.receptor_path = str(Path("targets") / target_name / target_config["receptor"])
        pocket_residue_ids = set(target_config["pocket"]["residue_ids"])
        self.pocket_contact_cutoff = target_config["pocket"].get("contact_cutoff", 3.5)
        self.protein_residues = parse_receptor_residues(self.receptor_path, pocket_residue_ids)

        docking_cfg = target_config.get("docking", global_config.get("docking", {}))
        self.engine = docking_cfg.get("engine", "gnina")
        self.docking_config = self._build_docking_config(target_config, global_config)
        self.oracle_config = self._build_oracle_config(target_config, global_config)
        self.essential = self._build_essential(target_config, global_config, pocket_residue_ids)
        self._essential_path = self.layout.coverage_path.parent / "essential.json"

        self._start_time = time.time()
        self._last_checkpoint_time = self._start_time
        self._last_checkpoint_iteration = -1
        self._total_docks = 0
        self._completed_docks = 0
        self._last_gpu_active = None
        self._pool: ProcessPoolExecutor | None = None

        if resuming:
            self._restore_checkpoints()
        elif seeds_path:
            self._load_seeds(seeds_path)

    # ---------------- configuration ----------------

    def _build_coverage(self, target_config: dict, global_config: dict) -> CoverageMap:
        pocket_residue_ids = set(target_config["pocket"]["residue_ids"])
        coverage_cfg = global_config.get("coverage", {})
        return CoverageMap(
            pocket_residue_ids=pocket_residue_ids,
            map_size_bytes=coverage_cfg.get("map_size_kib", 256) * 1024,
            occupancy_rotate_threshold=coverage_cfg.get("occupancy_rotate_threshold", 0.55),
            interaction_types_enabled=coverage_cfg.get("interaction_types", False),
            novelty_weights=coverage_cfg.get("novelty_weights"),
            contact_cutoff=target_config["pocket"].get("contact_cutoff", 3.5),
        )

    def _build_docking_config(self, target_config: dict, global_config: dict) -> DockingConfig:
        docking_cfg = target_config.get("docking", global_config.get("docking", {}))
        box = target_config["box"]
        return DockingConfig(
            receptor_path=self.receptor_path,
            center_x=box["center_x"], center_y=box["center_y"], center_z=box["center_z"],
            size_x=box["size_x"], size_y=box["size_y"], size_z=box["size_z"],
            exhaustiveness=docking_cfg.get("exhaustiveness_fuzz", 8),
            num_modes=docking_cfg.get("num_modes", 3),
            timeout_seconds=docking_cfg.get("timeout_seconds", 300),
            workers=self.workers,
            cnn_model=docking_cfg.get("cnn_model_fuzz", "fast"),
        )

    @staticmethod
    def _merged_oracle_cfg(target_config: dict, global_config: dict) -> dict:
        oracle_cfg = dict(global_config.get("oracle", {}))
        oracle_cfg.update(target_config.get("oracle", {}))
        return oracle_cfg

    def _build_oracle_config(self, target_config: dict, global_config: dict) -> OracleConfig:
        oracle_cfg = self._merged_oracle_cfg(target_config, global_config)
        return OracleConfig(
            affinity_threshold=oracle_cfg.get("affinity_threshold", -9.0),
            strain_threshold=oracle_cfg.get("strain_threshold", 3.5),
            scoring_policy=oracle_cfg.get("scoring_policy", "consensus"),
            min_ligand_efficiency=oracle_cfg.get("min_ligand_efficiency", 0.22),
            min_cnn_pose_score=oracle_cfg.get("min_cnn_pose_score", 0.4),
            max_score_disagreement=oracle_cfg.get("max_score_disagreement"),
            min_essential_contacts=oracle_cfg.get("min_essential_contacts", 1),
        )

    def _build_essential(
        self, target_config: dict, global_config: dict, pocket_residue_ids: set
    ) -> EssentialResidues | None:
        """Assemble the essential-residue set from the protein alone.

        Disabled (returns None) when the oracle's essential tier is off. The
        static set is taken from the target's `pocket.essential_residue_ids` when
        the target-prep tool (or a human) has written one; otherwise it is derived
        here from receptor burial + polar character, so a target that knows
        nothing about itself still gets a set. The emergent half accrues at
        runtime as seeds calibrate (see _process_results). See
        docs/architecture.md.
        """
        if self.oracle_config.min_essential_contacts is None:
            return None

        oracle_cfg = self._merged_oracle_cfg(target_config, global_config)
        static_ids = list(target_config["pocket"].get("essential_residue_ids") or [])
        if not static_ids:
            all_residues = parse_receptor_residues(self.receptor_path)
            static_ids = select_static_essential(
                all_residues,
                pocket_residue_ids,
                count=oracle_cfg.get("essential_static_count", 4),
            )
            self.log.write(
                "INFO",
                f"[ESSENTIAL] derived {len(static_ids)} residues from structure: "
                f"{', '.join(static_ids) or 'none'}",
            )

        return EssentialResidues(
            static=set(static_ids),
            min_contacts=self.oracle_config.min_essential_contacts,
            emergent_fraction=oracle_cfg.get("essential_emergent_fraction", 0.6),
            emergent_min_poses=oracle_cfg.get("essential_emergent_min_poses", 3),
            emergent_min_pose_score=oracle_cfg.get("essential_emergent_min_pose_score", 0.6),
            max_active=oracle_cfg.get("essential_max_active", 6),
        )

    # ---------------- setup ----------------

    def _load_seeds(self, seeds_path: str) -> None:
        # The corpus is seeded only from the target-agnostic approved-drug set.
        # A target's own known inhibitor is deliberately not injected here and
        # gets no priority boost: discovery must not be handed the answer. If a
        # known binder happens to already be in seeds/approved_drugs.smi it
        # competes on the same drug-likeness prior as everything else.
        #
        # The seed prior goes to `base_priority`, the entry's intrinsic worth.
        # Writing it to `priority` instead would lose it: Corpus derives that
        # field from base_priority minus scaffold crowding on every insert.
        #
        # Scaffold counts are read back from the corpus as it fills, so a seed
        # whose scaffold is already represented earns a smaller diversity bonus
        # than the first of its series. Without this the bonus is a constant and
        # cannot order seeds at all.
        for smiles, seed_id in load_seeds(seeds_path):
            priority = compute_seed_priority(
                smiles, corpus_scaffold_counts=self.state.corpus.scaffold_counts()
            )
            self.state.corpus.add(
                CorpusEntry(smiles=smiles, source_id=seed_id, base_priority=priority)
            )

        self.log.write("INFO", f"[SEED] adding {self.state.corpus.size()} seeds to corpus")

    def _restore_checkpoints(self) -> None:
        corpus_path = self.layout.corpus_dir / "state.json"
        if corpus_path.exists():
            self.state.corpus.load(corpus_path)
            for smiles, entry in self.state.corpus._entries.items():
                if entry.calibrated:
                    self.state.evaluated.add(smiles)
            self.log.write(
                "INFO",
                f"[RESUME] corpus restored: {self.state.corpus.size()} entries, "
                f"{len(self.state.evaluated)} already docked",
            )
        else:
            self.log.write("WARN", f"[RESUME] no corpus checkpoint at {corpus_path}")

        if self.layout.coverage_path.exists():
            try:
                self.state.coverage.load(self.layout.coverage_path)
                self.log.write(
                    "INFO",
                    f"[RESUME] coverage restored: epoch={self.state.coverage.epoch} "
                    f"union={self.state.coverage.union_coverage():.3f}",
                )
            except CheckpointVersionError as exc:
                self.log.write("WARN", f"[RESUME] coverage checkpoint unusable, starting fresh: {exc}")
        else:
            self.log.write("WARN", f"[RESUME] no coverage checkpoint at {self.layout.coverage_path}")

        if self.essential is not None and self._essential_path.exists():
            self.essential.load_state(load_checkpoint(self._essential_path))
            self.log.write(
                "INFO",
                f"[RESUME] essential-residue set restored: "
                f"{', '.join(sorted(self.essential.active())) or 'static only'}",
            )

    # ---------------- status ----------------

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
            union_coverage=self.state.coverage.union_coverage(),
            distinct_scaffolds=self.state.corpus.distinct_scaffolds(),
            docks_skipped=self.state.docks_skipped,
        )
        if self.ui_callback is not None:
            self.ui_callback(status)
        if self.ui is not None:
            self.ui.update(status)

    # ---------------- work ----------------

    def _select_stage(self, entry: CorpusEntry) -> tuple[str, str | None]:
        # A donor is only needed for splice; sample one up front so the
        # scheduler knows whether that stage is available at all.
        donor = self.state.corpus.sample_donor(exclude_smiles=entry.smiles)
        stage = select_stage(entry, self._rng, has_donor=donor is not None)
        return stage, donor.smiles if (stage == "splice" and donor is not None) else None

    def _pool_or_none(self) -> ProcessPoolExecutor | None:
        """One pool for the whole campaign, created on first use.

        The pool outlives individual iterations deliberately: tearing it down
        per batch would make every batch pay full interpreter start-up, plus a
        fresh RDKit import per worker, before any docking began.
        """
        if self.workers <= 1:
            return None
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=self.workers)
        return self._pool

    def _shutdown_pool(self, cancel_futures: bool = False) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=cancel_futures)
            self._pool = None

    def _prepare_mutants(self, mutants) -> list[tuple[object, str]]:
        """SMILES -> PDBQT for each mutant, skipping ones already docked.

        3D embedding plus MMFF optimisation is the second-largest cost in the
        loop after docking itself, and it was running serially on the main
        process while the worker pool idled.
        """
        pending: list = []
        prepared: list[tuple[object, str]] = []

        for mutant in mutants:
            if mutant.smiles in self.state.evaluated:
                self.state.docks_skipped += 1
                continue
            cached = self.state.cache.get(mutant.smiles)
            if cached is not None:
                prepared.append((mutant, cached))
            else:
                pending.append(mutant)

        if not pending:
            return prepared

        pool = self._pool_or_none()
        if pool is None:
            for mutant in pending:
                pdbqt = prepare_smiles(mutant.smiles, **self.filter_kwargs)
                if pdbqt is None:
                    self.state.prep_failures += 1
                    continue
                self.state.cache.set(mutant.smiles, pdbqt)
                prepared.append((mutant, pdbqt))
            return prepared

        futures = {
            pool.submit(prepare_worker, mutant.smiles, self.filter_kwargs): mutant
            for mutant in pending
        }
        for future in as_completed(futures):
            mutant = futures[future]
            try:
                pdbqt = future.result()
            except Exception:
                pdbqt = None
            if pdbqt is None:
                self.state.prep_failures += 1
                continue
            self.state.cache.set(mutant.smiles, pdbqt)
            prepared.append((mutant, pdbqt))
        return prepared

    def _dock_all(
        self, prepared: list[tuple[object, str]], mutation_stage: str = ""
    ) -> list[tuple[object, object]]:
        results = []
        self._total_docks += len(prepared)
        # Each dock can take tens of seconds on CPU; emit after every
        # completion (not just once per batch) so the UI reflects progress
        # instead of appearing frozen for the whole batch.
        self._emit_status("dock", mutation_stage=mutation_stage)

        pool = self._pool_or_none()
        if pool is not None and len(prepared) > 1:
            futures = {
                pool.submit(dock_worker, pdbqt, self.docking_config, self.engine): mutant
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
                    self._emit_status("dock", mutation_stage=mutation_stage)
            except KeyboardInterrupt:
                self._shutdown_pool(cancel_futures=True)
                raise
        else:
            backend = get_backend(self.engine)
            for mutant, pdbqt in prepared:
                result = backend.dock(pdbqt, self.docking_config)
                results.append((mutant, result))
                self._completed_docks += 1
                if result.gpu_active is not None:
                    self._last_gpu_active = result.gpu_active
                self._emit_status("dock", mutation_stage=mutation_stage)

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
                if not modes:
                    # gnina exited cleanly but produced no scored pose. There is
                    # no measurement here: evaluate() would report affinity 0.0,
                    # which would then be recorded as though it were real.
                    self.state.evaluated.add(mutant.smiles)
                    continue

                # Which pocket residues this pose contacts. Reused for both the
                # essential-residue tier and the emergent-set calibration below;
                # computed plainly (no interaction typing) so it is a set of bare
                # residue ids regardless of the coverage map's mode.
                contacts = build_fingerprint(
                    pose_atoms,
                    self.protein_residues,
                    contact_cutoff=self.pocket_contact_cutoff,
                    interaction_types_enabled=False,
                )
                essential_contacts = None
                if self.essential is not None and self.essential.is_gating():
                    essential_contacts = self.essential.engaged_count(contacts)

                verdict = evaluate(
                    modes,
                    pose_text,
                    self.oracle_config,
                    smiles=mutant.smiles,
                    essential_contacts=essential_contacts,
                )

                # A calibration (seed) pose votes on the emergent essential set,
                # but only after it has been gated -- a pose never helps define
                # the set it is judged against.
                if self.essential is not None and mutant.stage == "calibration":
                    self.essential.observe_calibration(contacts, verdict.cnn_pose_score)

                self.state.evaluated.add(mutant.smiles)

                if verdict.is_hit:
                    saved = self.state.findings.save(
                        smiles=mutant.smiles,
                        verdict={"passed_tiers": verdict.passed_tiers, "notes": verdict.notes},
                        pose_path=result.pose_path,
                        affinity=verdict.affinity,
                        strain=verdict.strain,
                        mutation_stage=mutant.stage,
                        mutation_type=mutant.mutation_type,
                        parent_smiles=mutant.parent_smiles,
                        corpus_source_id=entry.source_id,
                        ligand_efficiency=verdict.ligand_efficiency,
                        heavy_atom_count=verdict.heavy_atom_count,
                        vina_affinity=verdict.vina_affinity,
                        cnn_affinity_kcal=verdict.cnn_affinity_kcal,
                        cnn_pose_score=verdict.cnn_pose_score,
                        scoring_policy=verdict.scoring_policy,
                        novelty_class=obs.novelty_class,
                        new_coverage_bits=obs.new_bits,
                        essential_contacts=verdict.essential_contacts,
                    )
                    if saved is not None:
                        self.state.hits += 1
                        le_text = (
                            f" LE={verdict.ligand_efficiency:.2f}"
                            if verdict.ligand_efficiency is not None
                            else ""
                        )
                        # FuzzerLog already renders the level, so the message
                        # itself must not repeat it. The UI log ring has no
                        # level column, so it gets the prefix.
                        summary = (
                            f"{mutant.smiles} | affinity={verdict.affinity:.2f}{le_text}"
                        )
                        self.log.write("HIT", summary)
                        if self.ui is not None:
                            self.ui.log(f"[HIT] {summary}")

                if self.state.best_affinity is None or verdict.affinity < self.state.best_affinity:
                    self.state.best_affinity = verdict.affinity

                added = self.state.corpus.add(
                    mutant.smiles,
                    novelty=obs.novelty_score,
                    affinity=verdict.affinity,
                    mutation_type=mutant.mutation_type,
                    rarity=obs.rarity,
                    ligand_efficiency=verdict.ligand_efficiency,
                )
                added.calibrated = True
            finally:
                if result.pose_path and os.path.exists(result.pose_path):
                    os.unlink(result.pose_path)

    def _calibrate(self, entry: CorpusEntry) -> None:
        """Dock the entry itself, once, before fuzzing its mutants.

        This is AFL++'s calibration exec: run the input itself, learn what it
        does, then fuzz it. Docking only an entry's mutants would leave two
        things broken. The drug-repurposing question -- does this approved drug
        bind this target? -- would never be asked, and every seed would keep
        best_affinity=None, leaving the power schedule to rank seeds on no
        evidence at all.
        """
        if entry.calibrated or entry.smiles in self.state.evaluated:
            entry.calibrated = True
            return

        self._emit_status("calibrate", current_parent=entry.smiles)

        pdbqt = self.state.cache.get(entry.smiles)
        if pdbqt is None:
            pdbqt = prepare_smiles(entry.smiles, **self.filter_kwargs)
            if pdbqt is None:
                # Logged at WARN, not skipped silently: a corpus entry that
                # cannot be prepared is a molecule this campaign will never
                # test, and the cause is almost always the `molecules` bounds
                # rejecting it rather than a defect in its chemistry.
                self.state.prep_failures += 1
                self.log.write(
                    "WARN",
                    f"[PREP] cannot prepare corpus entry, it will never be docked: "
                    f"{entry.smiles} (source={entry.source_id or 'mutant'}; "
                    f"check molecules bounds, e.g. max_mw={self.filter_kwargs['max_mw']})",
                )
                entry.calibrated = True
                self.state.evaluated.add(entry.smiles)
                return
            self.state.cache.set(entry.smiles, pdbqt)

        candidate = MutationCandidate(
            smiles=entry.smiles,
            stage="calibration",
            mutation_type="seed",
            parent_smiles=entry.smiles,
        )
        results = self._dock_all([(candidate, pdbqt)], mutation_stage="calibration")
        self._process_results(entry, results)
        entry.calibrated = True

    def run_iteration(self) -> bool:
        entry = self.state.corpus.pop()
        if entry is None:
            return False

        if not entry.calibrated:
            self._calibrate(entry)

        stage, donor_smiles = self._select_stage(entry)
        budget = compute_mutation_budget(entry, self.base_mutations)

        self._emit_status(
            "mutate", mutation_stage=stage, current_parent=entry.smiles, mutation_budget=budget
        )

        mutants = mutate_with_metadata(
            entry.smiles, n=budget, stage=stage, donor_smiles=donor_smiles, **self.filter_kwargs
        )

        prepared = self._prepare_mutants(mutants)

        results = self._dock_all(prepared, mutation_stage=stage)

        self._process_results(entry, results)

        entry.times_fuzzed += 1
        # Reprice from this entry's own evidence; Corpus.add applies the
        # scaffold-crowding discount on top when it re-queues.
        entry.base_priority = self.state.corpus.compute_priority(entry)
        self.state.corpus.add(entry)

        self.state.iterations += 1

        if self._should_checkpoint():
            self.checkpoint()

        self._emit_status("idle", mutation_stage=stage)
        return True

    def _should_checkpoint(self) -> bool:
        """Checkpoint on iterations *or* elapsed time, whichever comes first.

        Iteration count alone is a bad clock here: one iteration is a whole
        batch of docks, so at CPU docking speeds `checkpoint_every=500` can be
        days of work. Without the wall-clock bound, a campaign can run for hours
        and leave nothing resumable behind.
        """
        if self.checkpoint_every and self.state.iterations % self.checkpoint_every == 0:
            return True
        if self.checkpoint_every_seconds:
            return (time.time() - self._last_checkpoint_time) >= self.checkpoint_every_seconds
        return False

    def checkpoint(self, force: bool = True) -> None:
        """Persist corpus, coverage and essential-residue state.

        `force=False` skips the write when no iteration has completed since the
        last checkpoint, so the final checkpoint on exit does not duplicate the
        one the last iteration just wrote.
        """
        if not force and self.state.iterations == self._last_checkpoint_iteration:
            return
        self.state.corpus.save(self.layout.corpus_dir / "state.json")
        self.state.coverage.save(self.layout.coverage_path)
        if self.essential is not None:
            # Emergent tallies only: the static set is rederived at startup. Most
            # seeds are already calibrated on resume and so are never re-docked,
            # which is exactly why this signal has to survive a restart -- without
            # it the emergent set would reset to empty and never rebuild.
            save_checkpoint(self._essential_path, {"version": 1, **self.essential.state_dict()})
        self.state.checkpoint_count += 1
        self._last_checkpoint_time = time.time()
        self._last_checkpoint_iteration = self.state.iterations
        self.log.write(
            "CHKPT",
            f"iterations={self.state.iterations} "
            f"occupancy={self.state.coverage.bitmap_occupancy():.3f} "
            f"union={self.state.coverage.union_coverage():.3f} "
            f"epoch={self.state.coverage.epoch} corpus={self.state.corpus.size()} "
            f"scaffolds={self.state.corpus.distinct_scaffolds()} hits={self.state.hits}",
        )

    def _finish(self) -> None:
        self.log.write(
            "INFO",
            f"campaign stopped: {self.state.stopped_reason} "
            f"iterations={self.state.iterations} hits={self.state.hits} "
            f"docks={self._completed_docks} skipped={self.state.docks_skipped}",
        )
        self.log.close()
        self._shutdown_pool()
        if self.ui is not None:
            self.ui.close()

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
            # Always forced: an interrupt can land mid-iteration, and the
            # partial progress is exactly what needs saving.
            self.checkpoint()
            self._finish()
            raise

        self.checkpoint(force=False)
        self._finish()
        return self.state
