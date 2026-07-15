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
   Load config.
   Initialize Corpus, CoverageMap, FindingsStore, PDBQTCache.
   Load checkpoints if a prior run exists in output_dir.
   Add seed molecules directly to Corpus (no docking at this stage).

2. MAIN LOOP  (runs until Ctrl-C or --max-iterations reached)
   entry = corpus.pop()
   budget = power_schedule(entry)
   mutants = mutator.mutate(entry.smiles, budget, stage=entry.next_stage())

   For each mutant (in parallel via worker pool):
     pdbqt = prep.prepare(mutant.smiles)            // skip if None
     result = docker.dock(pdbqt, target_config)      // skip if failed

   For each completed result (sequential, in main thread):
     obs = coverage.observe(pose_atoms, protein_residues)
     verdict = oracle.evaluate(result.affinity, result.pose_text)
     if verdict.is_hit:
         storage.save_finding(mutant.smiles, result, verdict)
     corpus.add(mutant.smiles, novelty=obs.novelty_score, affinity=result.affinity)

   corpus.add(entry)   // requeue parent (AFL++ persistent queue)

3. CHECKPOINT  (every N iterations and on any exit)
   corpus.save(output_dir / "corpus/state.json")
   coverage.save(output_dir / "coverage.json")

4. EXIT
   Terminate worker pool cleanly.
   Save final checkpoints.
   Report summary stats to stdout.
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

Workers are used only for docking (the bottleneck). Preparation runs in the main thread before jobs are dispatched. Coverage and corpus updates always run in the main thread after results return — neither is thread-safe.

Preferred implementation: `concurrent.futures.ProcessPoolExecutor` with a fixed worker count. Jobs are submitted as a batch per mutation round. Results are collected as they complete.

On Ctrl-C:
1. Signal workers to stop (SIGINT suppressed in workers; they finish current dock or timeout)
2. Drain pending results
3. Save checkpoints
4. Exit with code 130

---

## Progress Reporting

The fuzzer emits a `RuntimeStatus` dataclass to the UI via an optional callback. The callback is never called from worker processes — only from the main thread. The fuzzer does not know or care what the callback does (TUI, JSON log, or nothing).

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

---

## CLI Interface (`biofuzz-fuzz`)

```
biofuzz-fuzz --target <name> [options]

Required:
  --target          Target name (looks up targets/<name>/config.yaml)

Optional:
  --seeds           Path to seed SMILES file (default: seeds/approved_drugs.smi)
  --output          Output directory (default: runs/<date>_<target>)
  --max-iterations  Stop after N docking iterations (default: unlimited)
  --workers         Worker count (default: from config.yaml)
  --config          Global config path (default: config.yaml)
```

---

## Build Criterion

Run for 10 iterations on a bundled target:

```
biofuzz-fuzz --target hiv_protease --max-iterations 10 --workers 1
```

Verify:
- Run completes without error
- `runs/*/corpus/state.json` exists and contains entries
- `runs/*/coverage.json` exists
- No worker processes left hanging after exit
- Ctrl-C at any point during the run produces clean checkpoints and exits with code 130
