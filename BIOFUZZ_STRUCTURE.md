# BioFuzz — Architecture Overview

BioFuzz applies coverage-guided fuzzing to molecular docking. The protein is the program, the molecule is the input, and docking is execution. The design mirrors AFL++ closely: a persistent corpus queue driven by a hashed-fingerprint coverage bitmap, with mutation stages modeled after AFL++'s deterministic → splice → havoc pipeline.

Two separate tools mirror AFL++'s own split between fuzzing and bug analysis:

- **`biofuzz-fuzz`** — the fuzzing loop. Fast, parallel, coverage-driven. The in-loop oracle does only affinity + strain gating, nothing expensive.
- **`biofuzz-triage`** — post-fuzzing hit analysis. The CASR analog. Slow, thorough, never in the hot path.

---

## The Analogy

| AFL++ | BioFuzz |
|---|---|
| Target binary | Protein (PDBQT + box config) |
| Input | Molecule (SMILES / PDBQT) |
| Execution (fork server) | Docking run (gnina) |
| Edge coverage bitmap | Residue-contact fingerprint bitmap |
| Crash | High-affinity docking hit |
| Crash triage (CASR) | `biofuzz-triage` |
| Deterministic stage | Systematic atom / substituent scan |
| Splice stage | Scaffold + R-group crossover |
| Havoc stage | Stochastic chemical mutation |
| Power schedule | Novelty × affinity weighted budget |
| Seed corpus | Curated approved small-molecule drugs |
| Favored entry | Entry that uniquely covers a fingerprint bucket |

---

## Repository Layout

```
BioFuzz/
├── biofuzz/
│   ├── corpus/       # Corpus queue and power schedule
│   ├── coverage/     # Fingerprint bitmap and novelty classification
│   ├── docker/       # Docking backend interface (default: gnina)
│   ├── fuzzer/       # Main fuzzing campaign
│   ├── mutator/      # Mutation stages
│   ├── oracle/       # In-loop hit detection
│   ├── prep/         # SMILES → PDBQT preparation pipeline
│   ├── protein/      # Protein structure utilities
│   ├── storage/      # Findings, cache, checkpoints
│   ├── triage/       # Post-fuzzing analysis
│   └── ui/           # Display interface (pluggable)
├── docs/
│   └── modules/      # Per-module design documents (this directory)
├── seeds/
│   ├── approved_drugs.smi    # Curated FDA-approved small molecule SMILES
│   └── per_target/           # Target-specific known binders
├── targets/
│   └── <name>/
│       ├── protein.pdbqt     # Prepared receptor
│       └── config.yaml       # Box, pocket, oracle thresholds
├── runs/
│   └── <stamp>_<target>/
│       ├── findings/         # Hit pose + metadata per find
│       ├── corpus/           # Checkpoint state.json
│       └── coverage.json     # Bitmap checkpoint
├── config.yaml               # Global configuration defaults
├── requirements.txt
├── biofuzz-fuzz              # Fuzzing entry point
└── biofuzz-triage            # Post-fuzzing triage entry point
```

---

## Module Index

Each module is fully documented in `docs/modules/`. Modules communicate through well-defined data contracts and do not import from sibling modules except through those contracts. Each module can be built, tested, and replaced in isolation.

| Module | AFL++ Analog | Document | One-line role |
|---|---|---|---|
| Fuzzer | `afl-fuzz` main loop | [fuzzer.md](docs/modules/fuzzer.md) | Campaign orchestration and worker management |
| Corpus | `queue/` + power schedule | [corpus.md](docs/modules/corpus.md) | Prioritized molecule queue |
| Mutator | Mutation stages | [mutator.md](docs/modules/mutator.md) | Chemical graph mutations |
| Coverage | `__afl_area_ptr` bitmap | [coverage.md](docs/modules/coverage.md) | Fingerprint novelty tracking |
| Docking Backend | Target execution | [docker.md](docs/modules/docker.md) | Pluggable docking interface (gnina) |
| Preparation | Input format conversion | [prep.md](docs/modules/prep.md) | SMILES → 3D → PDBQT |
| Oracle | Crash detector | [oracle.md](docs/modules/oracle.md) | Fast in-loop hit gating |
| Triage | CASR / exploitability | [triage.md](docs/modules/triage.md) | Deep post-fuzzing hit analysis |
| Seeds | Seed corpus | [seeds.md](docs/modules/seeds.md) | Curated known-drug inputs |
| UI | AFL++ status screen | [ui.md](docs/modules/ui.md) | Pluggable progress display |
| Storage | `queue/`, `crashes/` | [storage.md](docs/modules/storage.md) | Persistence layer |

---

## Build Order

Build and verify each phase in sequence. Completion criteria are defined in each module document. Do not advance until the phase works end-to-end.

```
Phase 1  →  Preparation         SMILES → valid PDBQT
Phase 2  →  Docking Backend     PDBQT + receptor → affinity + pose
Phase 3  →  Coverage            Pose atoms → fingerprint → novelty class
Phase 4  →  Oracle              affinity + pose → hit / no-hit
Phase 5  →  Corpus              Entries in / highest priority out + checkpoint
Phase 6  →  Mutator             SMILES → valid mutants across all three stages
Phase 7  →  Fuzzer              Wire 1–6; runs until Ctrl-C; saves checkpoints
Phase 8  →  Seeds               Curated seed file + loader (no pre-docking)
Phase 9  →  UI                  RuntimeStatus → display, decoupled from loop
Phase 10 →  Triage              findings/ → ranked annotated report
```

---

## Data Flow

```
seeds/approved_drugs.smi
        │
        ▼
    Corpus.add() (direct — no pre-docking)
        │◄──────────────────────────────────────────────┐
        ▼                                               │
    Corpus.pop() → entry (power schedule)               │
        │                                               │
        ▼                                               │
    Mutator.mutate(entry, budget)                       │
        [deterministic → splice → havoc]                │
        │                                               │
        ▼  (parallel, one job per worker)               │
    Prep.prepare(smiles) → PDBQT                        │
        │ fail → discard                                │
        ▼                                               │
    Docker.dock(PDBQT, target_config) → DockingResult   │
        │                                               │
        ├──→ Coverage.observe(pose_atoms)               │
        │          │                                    │
        │          └──→ novelty_score                   │
        │                    │                          │
        ├──→ Oracle.evaluate(affinity, strain)          │
        │          │                                    │
        │          └──→ hit? → Storage.save_finding()   │
        │                                               │
        └──→ Corpus.add(smiles, novelty, affinity) ────┘

 ── after campaign ends ──

  runs/<stamp>/findings/
        │
        ▼
    biofuzz-triage
        │
        ▼
    Ranked, annotated hit report
```

---

## Configuration

Global defaults live in `config.yaml`. Per-target overrides live in `targets/<name>/config.yaml`. There is no Python module execution for configuration — all config files are YAML.

The docking backend, preparation backend, and UI are all configured by name in `config.yaml`. Swapping any of them requires only a config change and an implementation in the appropriate module directory.

---

## Key Design Decisions

**Gnina only.** The docking backend defaults to gnina because its CNN scoring function is meaningfully more accurate than Vina's empirical function. The interface is abstract (see [docker.md](docs/modules/docker.md)) so other engines can be added, but gnina is the only bundled implementation.

**No seed triage.** Seeds are trusted approved drugs — known to bind real targets. They enter the corpus directly at startup without pre-docking. The fuzzer's normal loop handles them on first pop. This mirrors AFL++ where seeds go straight into the queue; calibration is done lazily, not as a blocking triage phase.

**Triage is post-fuzzing.** Selectivity checks, ADMET filters, ligand efficiency, pose quality analysis, and binding mode clustering are all part of `biofuzz-triage`, not the fuzzing loop. The in-loop oracle gates only on affinity and strain — both derivable from a single docking result. Everything expensive happens after the campaign.

**Modular backends.** The docking backend, preparation backend, and UI are defined by interface contracts. The fuzzer imports only the contract, not the implementation. Swapping gnina for another engine, or the terminal TUI for a web dashboard, requires no changes to the fuzzer.
