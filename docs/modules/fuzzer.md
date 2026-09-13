# Module: Fuzzer

**AFL++ analog:** `afl-fuzz` main loop

The fuzzer is the campaign orchestrator. It owns the top-level run lifecycle: loading seeds, driving the mutation → preparation → docking → coverage → corpus loop, managing parallel workers, emitting progress, and saving checkpoints. It contains no mutation logic, no oracle logic, and no display logic — it delegates all of those to their respective modules and wires them together.

---

## Boundary

**Does:**
- Load seeds into corpus at startup (no pre-docking)
- Run the main loop: pop → mutate → prepare → dock → observe → add
- Manage the worker pool for parallel docking
- Checkpoint corpus and coverage periodically and on exit
- Emit `RuntimeStatus` to the UI callback on state transitions
- Handle Ctrl-C cleanly: finish in-flight docks, save checkpoints, exit

**Does not:**
- Decide which mutations to apply (Mutator)
- Decide whether a result is a hit (Oracle)
- Compute coverage novelty (Coverage)
- Render any output (UI)
- Define what "preparation" or "docking" means (Prep, Docker)

---

## Lifecycle

```
1. STARTUP
   Load and merge global + target config.
   Initialize Corpus, CoverageMap, FindingsStore, PDBQTCache.
   Parse the receptor's pocket residues; derive the essential-residue set.
   If --resume: restore corpus, coverage and emergent essential tallies.
   Otherwise: add seed molecules directly to Corpus (no docking at this stage).

2. MAIN LOOP  (runs until Ctrl-C or --max-iterations reached)
   entry = corpus.pop()                  // increments times_selected

   if not entry.calibrated:              // AFL++'s calibration exec
       dock the entry ITSELF, once, and process the result

   stage, donor = select_stage(entry)    // deterministic -> splice/havoc
   budget = mutation_budget(entry)       // power schedule
   mutants = mutator.mutate(entry.smiles, budget, stage, donor)

   prepared = prepare_mutants(mutants)   // in the worker pool; skips
                                         // already-evaluated and cached SMILES
   results = dock_all(prepared)          // in the worker pool

   For each result (sequential, on the main process):
     obs = coverage.observe(pose_atoms, protein_residues, smiles)
     contacts = build_fingerprint(...)   // for the essential-residue tier
     verdict = oracle.evaluate(modes, pose_text, cfg, smiles, essential_contacts)
     if verdict.is_hit:  findings.save(...)     // deduped by SMILES
     corpus.add(mutant.smiles, novelty=..., affinity=..., rarity=...)

   entry.times_fuzzed += 1
   entry.base_priority = corpus.compute_priority(entry)
   corpus.add(entry)                     // requeue parent (persistent queue)

3. CHECKPOINT  (every N iterations OR every N seconds, whichever first)
   corpus.save(run_dir / "corpus/state.json")
   coverage.save(run_dir / "coverage.json")
   save emergent essential-residue tallies to run_dir / "essential.json"

4. EXIT
   Save final checkpoints (including on KeyboardInterrupt, before re-raising).
   Shut down the worker pool; close the log; close the UI.
```

---

## Campaign State

The fuzzer maintains campaign state as a dataclass or class, not as closure variables. Every mutable field that affects the loop is a named attribute on the campaign object.

Key fields:
- `corpus: Corpus`
- `coverage: CoverageMap`
- `findings: FindingsStore`
- `cache: PDBQTCache`
- `iterations: int`
- `hits: int`
- `best_affinity: float | None`
- `checkpoint_count: int`
- `stopped_reason: str`

---

## Parallelism

Workers handle **both** docking and preparation. Docking is the bottleneck, but
conformer embedding and force-field optimisation are the second largest cost and
are independent per molecule, so running them on the main process would leave the
pool idle for a significant fraction of each batch.

Coverage, oracle and corpus updates always run on the main process after results
return. None of them is process-safe, and none is expensive enough to be worth
making so.

Implementation: one `concurrent.futures.ProcessPoolExecutor` for the whole
campaign, created lazily on first use. It is deliberately not created per
iteration — that would make every batch pay interpreter start-up and a fresh
RDKit import per worker before any docking began. With `workers <= 1` no pool is
created and the backend is called inline.

On Ctrl-C:
1. `KeyboardInterrupt` propagates out of the result loop
2. The pool is shut down with `cancel_futures=True`, and `GninaBackend` kills its
   child process before letting the exception through, so no gnina survives
3. A checkpoint is written and the log and UI are closed
4. The exception is re-raised; the CLI reports it and exits with code 130

---

## Progress Reporting

The fuzzer emits a `RuntimeStatus` dataclass to the UI. It is never emitted from
a worker process, only from the main process, and the fuzzer does not know or care
what the receiver does with it (TUI, JSON log, or nothing).

Two channels exist, because one is not enough. `ui_callback` is a bare
`Callable[[RuntimeStatus], None]` for callers that want status only. `ui` is an
optional richer object exposing `.update(status)`, `.log(message)` and `.close()`,
because hit and notable-event text cannot travel through a status struct. Either,
both, or neither may be attached.

Status is emitted after **every** completed dock, not once per batch: a single
dock can take tens of seconds, and a per-batch update makes the display look
frozen.

`RuntimeStatus` fields (at minimum):
- `stage: str` — current phase (mutate / prepare / dock / oracle / checkpoint / idle)
- `total_docks: int`
- `completed_docks: int`
- `docks_per_sec: float`
- `corpus_size: int`
- `hits: int`
- `best_affinity: float | None`
- `coverage_bitmap_occupancy: float`
- `coverage_epoch: int`
- `novelty_strong: int`, `novelty_weak: int`, `novelty_none: int`
- `elapsed_seconds: float`
- `power_score: float`
- `mutation_budget: int`
- `mutation_stage: str`
- `checkpoints: int`
- `union_coverage: float` — the interpretable coverage number
- `distinct_scaffolds: int`
- `docks_skipped: int` — molecules already evaluated this campaign
- `gpu_active: bool | None`

---

## CLI Interface (`biofuzz-fuzz`)

```
biofuzz-fuzz --target <name> [options]

Required:
  --target                     Target name (looks up targets/<name>/config.yaml)

Optional:
  --seeds PATH                 Seed SMILES file (default: seeds/approved_drugs.smi)
  --output DIR                 Base output dir (default: runs/<date>_<target>)
  --max-iterations N           Stop after N iterations (default: unlimited)
  --workers N                  Worker processes (default: from config.yaml)
  --checkpoint-every N         Checkpoint every N iterations
  --checkpoint-every-seconds S Also checkpoint on wall clock, whichever first
  --resume RUN_DIR             Continue a previous campaign in place
  --seed N                     RNG seed for stage and donor selection
  --config PATH                Global config path (default: config.yaml)
  --ui {tui,json,quiet,none}   Display mode (default: config, else TTY detection)
```

`--seed` fixes BioFuzz's own random choices only. The docking engine seeds its
search independently, so a run is not bit-reproducible.

Exit codes: 0 on completion, 2 if `--resume` names a directory that does not
exist, 130 on Ctrl-C.

---

## Verification

`tests/test_fuzzer.py` covers config loading and merge semantics, including that
no target ships its own `affinity_threshold`. `tests/test_calibration.py` covers
the calibration exec — that an entry is docked itself before its mutants, and
once only. `tests/test_resume.py` covers restoring a run in place and the
finding-dedup that stops a resumed campaign re-saving what it already found.

The interrupt path is tested by injecting `KeyboardInterrupt` from
`run_iteration()` and asserting the campaign checkpoints, closes its log, and
re-raises. Real OS signal delivery is not exercisable in-process, so that
injection plus the backend-level child-kill test
(`tests/test_docker.py::test_dock_process_killed_on_keyboard_interrupt`) is the
coverage available.

For a genuine end-to-end check, run a short campaign against a bundled target:

```sh
./biofuzz-fuzz --target hiv_protease --max-iterations 2 --workers 4 --seed 11
```

Expect `corpus/state.json`, `coverage.json` and `fuzzer.log` in the run
directory, a corpus larger than the seed count, and no orphaned gnina processes
afterwards. Budget several minutes per iteration on CPU.
