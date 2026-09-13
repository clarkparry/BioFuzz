# Architecture

BioFuzz applies coverage-guided fuzzing to molecular docking: the protein is the
program, the molecule is the input, and one docking run is one execution. The
design mirrors AFL++ closely — a persistent corpus queue driven by a
power schedule, a coverage map that decides what counts as novel, and mutation
stages modelled on AFL++'s deterministic → splice → havoc pipeline.

This document covers module boundaries and data flow. Each module has its own
design document under [`modules/`](modules/), and the user-facing overview is in
the [README](../README.md).

Two tools, mirroring AFL++'s own split between fuzzing and crash analysis:

- **`biofuzz-fuzz`** — the campaign loop. Parallel, coverage-driven, and
  deliberately cheap per molecule. The in-loop oracle only compares numbers the
  docking run already produced.
- **`biofuzz-triage`** — post-campaign hit analysis, the CASR analog. Slow,
  thorough, and never in the hot path.

---

## The analogy, and where it breaks

| AFL++ | BioFuzz |
|---|---|
| Target binary | Protein (receptor PDBQT + box config) |
| Input | Molecule (SMILES / PDBQT) |
| Execution (fork server) | Docking run (gnina) |
| Edge coverage bitmap | Residue-contact fingerprint map |
| Crash | Docking hit clearing every oracle tier |
| Calibration exec | Docking a corpus entry itself, before its mutants |
| Crash triage (CASR) | `biofuzz-triage` |
| Deterministic stage | Systematic atom / substituent / halogen scan |
| Splice stage | Scaffold and fragment crossover |
| Havoc stage | Stochastic chemical mutation |
| Power schedule | Novelty, affinity and rarity weighted budget |
| Seed corpus | Curated approved small-molecule drugs |
| Favored entry | Entry that pioneered a coverage slot |

**The analogy fails on the oracle, and that failure shapes everything else.** A
code fuzzer's oracle is definitive: a process either segfaulted or it did not.
A docking oracle is a scoring function with substantial known error, so a BioFuzz
hit is a hypothesis of uncertain quality rather than a proven defect. Three
design choices follow directly:

1. The oracle gates on the **consensus** of two independent scoring functions,
   not one, because each function's false positives are largely the other's
   rejects.
2. The gate is a **stack of cheap orthogonal tiers** rather than a single
   threshold, so that a molecule has to be plausible in several independent ways.
3. Everything expensive or judgement-laden is **deferred to triage**, where its
   output is advisory.

It is also why BioFuzz is positioned as a precursor to model- or expert-driven
triage, not a replacement for it.

---

## Module map

Modules communicate through explicit data contracts and do not import from
sibling modules except through those contracts.

| Module | AFL++ analog | Document | Role |
|---|---|---|---|
| Fuzzer | `afl-fuzz` main loop | [fuzzer.md](modules/fuzzer.md) | Campaign orchestration and worker management |
| Corpus | `queue/` + power schedule | [corpus.md](modules/corpus.md) | Prioritised molecule queue |
| Mutator | Mutation stages | [mutator.md](modules/mutator.md) | Chemical graph mutations |
| Coverage | `__afl_area_ptr` bitmap | [coverage.md](modules/coverage.md) | Contact novelty tracking |
| Docking backend | Target execution | [docker.md](modules/docker.md) | Docking engine contract (gnina) |
| Preparation | Input format conversion | [prep.md](modules/prep.md) | SMILES → 3D → PDBQT |
| Oracle | Crash detector | [oracle.md](modules/oracle.md) | Fast in-loop hit gating |
| Triage | CASR / exploitability | [triage.md](modules/triage.md) | Deep post-campaign analysis |
| Seeds | Seed corpus | [seeds.md](modules/seeds.md) | Curated known-drug inputs |
| UI | AFL++ status screen | [ui.md](modules/ui.md) | Pluggable progress display |
| Storage | `queue/`, `crashes/` | [storage.md](modules/storage.md) | Persistence layer |

`biofuzz/protein/` is a support library for Coverage and the Oracle rather than
a module in its own right: it parses receptor residues and derives the
essential-residue set. It has no separate design document; the essential-residue
derivation is specified in [oracle.md](modules/oracle.md) under Tier 6.

---

## Repository layout

```
BioFuzz/
├── biofuzz/
│   ├── cli/          # biofuzz-fuzz and biofuzz-triage entry points
│   ├── corpus/       # corpus queue and power schedule
│   ├── coverage/     # fingerprint maps and novelty classification
│   ├── docker/       # docking backend contract (default: gnina)
│   ├── fuzzer/       # campaign loop, config merging, worker functions
│   ├── mutator/      # mutation stages and structural alerts
│   ├── oracle/       # in-loop hit detection and scoring policy
│   ├── prep/         # SMILES -> PDBQT preparation pipeline
│   ├── protein/      # receptor parsing, essential-residue derivation
│   ├── seeds/        # seed loading and priors
│   ├── storage/      # findings, cache, checkpoints, run layout
│   ├── triage/       # post-campaign analysis
│   └── ui/           # display implementations
├── docs/
│   ├── architecture.md
│   ├── adding_targets.md
│   ├── evaluations/          # dated evaluation rounds
│   └── modules/              # one design document per module
├── seeds/approved_drugs.smi  # curated approved-drug seed corpus
├── targets/<name>/
│   ├── protein.pdbqt         # prepared receptor (generated, not committed)
│   ├── reference_ligands/    # optional known binders, validation only
│   └── config.yaml           # box + pocket, both ligand-free
├── tools/                    # installers, target prep, environment check
├── config.yaml               # global configuration defaults
├── biofuzz-fuzz              # wrapper for running without installing
└── biofuzz-triage
```

Campaign output goes to `runs/<date>_<target>/` and is gitignored; its layout is
in [storage.md](modules/storage.md).

---

## Data flow

```
seeds/approved_drugs.smi
        │
        ▼
    Corpus.add()  (direct, no pre-docking; prior from drug-likeness
        │          and scaffold diversity)
        │◄──────────────────────────────────────────────┐
        ▼                                               │
    Corpus.pop() → entry (power schedule)               │
        │                                               │
        ├─ not yet calibrated? dock the entry itself ──┐ │
        ▼                                             │ │
    Mutator.mutate(entry, budget)                     │ │
        [deterministic → splice → havoc]              │ │
        │                                             │ │
        ▼  (parallel across the worker pool)          │ │
    Prep.prepare(smiles) → PDBQT                      │ │
        │ fail → discard, count, continue              │ │
        ▼                                             ▼ │
    Docker.dock(PDBQT, target_config) → DockingResult   │
        │                                               │
        ├──→ Coverage.observe(pose_atoms, residues)     │
        │          └──→ novelty class, new bits, rarity  │
        │                                               │
        ├──→ Oracle.evaluate(modes, pose, config)       │
        │          └──→ hit? → Storage.save_finding()   │
        │                                               │
        └──→ Corpus.add(smiles, novelty, affinity) ─────┘

 ── after the campaign ──

    runs/<stamp>/findings/  →  biofuzz-triage  →  ranked annotated report
```

Preparation runs in the same worker pool as docking. Conformer embedding and
force-field optimisation are the second-largest cost in the loop, and running
them on the main process leaves the workers idle.

Coverage, oracle and corpus updates all run on the main process after results
return. None of them is process-safe, and none is expensive enough to be worth
making so.

---

## Configuration

Global defaults live in `config.yaml`; per-target overrides in
`targets/<name>/config.yaml`. The `docking`, `oracle`, `triage` and `molecules`
sections are merged key by key, so a target restates only what it changes.

All configuration is YAML. No configuration file is executable Python.

The **docking engine** and the **UI** are selected by name in config and
resolved through registries (`biofuzz.docker.get_backend`, and the UI mapping in
the CLI), so either can be swapped without touching the fuzzer. **Preparation is
not pluggable by config**: it is a single implementation behind the
`prepare_smiles` function contract, and swapping it means substituting that
function.

---

## Key design decisions

**gnina only.** The docking backend defaults to gnina because its CNN scoring
function adds an independent estimate of both affinity and pose plausibility that
vina's empirical function cannot provide. The backend interface is abstract (see
[docker.md](modules/docker.md)) and engines resolve by name, but gnina is the only
bundled implementation, and there is no vina fallback.

Choosing gnina only pays off if the CNN columns are actually read. Scoring policy
lives in [`oracle/scoring.py`](modules/oracle.md) and defaults to `consensus`:
vina's score and the CNN's prediction are put in the same units, and the
**weaker** of the two is what the oracle gates on. Running gnina and then ranking
on vina's number alone would pay CNN inference cost on every dock for nothing.

**No seed triage, but seeds are calibrated.** Seeds are approved drugs, trusted
without verification, so they enter the corpus directly at startup with no
blocking pre-docking phase — as in AFL++, where seeds go straight into the queue.

"No triage phase" is not "never docked". AFL++ calibrates each seed on its first
execution, and so does BioFuzz: the first time an entry is popped, the molecule
itself is docked before any of its mutants (`Campaign._calibrate`). This is what
makes repurposing possible at all. Without it the loop only ever docks mutants,
the question "does this approved drug bind this target?" is never asked, and the
power schedule ranks every seed on `best_affinity = None`.

**The oracle never consults a known inhibitor.** Nothing in the hit gate may be
derived from a drug you already have, because the premise is finding binders for
a protein you have none for, and a gate tuned to the answer inflates apparent
performance on exactly the bundled example targets. Every target inherits one
reference-free global oracle. The accepted cost is that a single absolute
affinity cutoff is coarse without a per-target anchor; the reference-free quality
tiers do the real discriminating, and per-target ranking belongs in triage.

**Boxes and pockets are ligand-free.** Both come from a pocket detector (P2Rank)
run on the apo receptor, not from residues near a co-crystallised inhibitor, so
the searched site and the coverage map are properties of the protein. A known
inhibitor is never required to add a target.

**Triage is post-fuzzing.** Selectivity, ADMET filters, pose-quality analysis,
PAINS screening and binding-mode clustering all belong to `biofuzz-triage`. The
in-loop oracle only compares numbers already present in a single docking result.

**Chemical plausibility is structural, not a property bound.** MW and logP say
nothing about whether a molecule can exist. Graph mutation readily produces
hydroxylamine ethers and hemiacetals that pass every property bound and dock
well, so `mutator/alerts.py` rejects them on substructure. That catalog is
deliberately narrow — it targets unstable motifs, not unattractive ones. General
medchem filters (PAINS, BRENK) are too aggressive for an in-loop gate and run in
triage instead, where a flag is advisory.

**Scaffold crowding is a first-class scheduler term.** Every mutant of a good
molecule is itself a good molecule, so a purely greedy queue collapses onto one
chemical series. Priority splits into intrinsic worth (`base_priority`) and a
crowding discount applied at selection time, so seed priors survive and runaway
lineages are demoted without being exiled.
