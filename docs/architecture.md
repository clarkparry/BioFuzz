# Architecture

BioFuzz applies coverage-guided fuzzing to molecular docking: the protein is the
program, the molecule is the input, and one docking run is one execution. The
design mirrors AFL++ closely — a persistent corpus queue driven by a power
schedule, a coverage map that decides what counts as novel, and mutation stages
modelled on AFL++'s deterministic → splice → havoc pipeline.

This is the only design document. It covers module boundaries, data flow, the
run directory layout, and the reasoning behind each default. The user-facing
overview is in the [README](../README.md), and dated measurement rounds are in
[evaluations/](evaluations/).

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

## Modules

Modules communicate through explicit data contracts and do not import from
sibling modules except through those contracts.

| Module | AFL++ analog | Role |
|---|---|---|
| Fuzzer | `afl-fuzz` main loop | Campaign orchestration and worker management |
| Corpus | `queue/` + power schedule | Prioritised molecule queue |
| Mutator | Mutation stages | Chemical graph mutations |
| Coverage | `__afl_area_ptr` bitmap | Contact novelty tracking |
| Docking backend | Target execution | Docking engine contract (gnina) |
| Preparation | Input format conversion | SMILES → 3D → PDBQT |
| Oracle | Crash detector | Fast in-loop hit gating |
| Triage | CASR / exploitability | Post-campaign analysis |
| Seeds | Seed corpus | Curated known-drug inputs |
| UI | AFL++ status screen | Terminal status screen, or quiet output |
| Storage | `queue/`, `crashes/` | Persistence layer |

`biofuzz/protein/` is a support library for Coverage and the Oracle rather than
a module in its own right: it parses receptor residues and derives the
essential-residue set.

```
BioFuzz/
├── biofuzz/                  # the package, one directory per module above
├── docs/
│   ├── architecture.md       # this document
│   ├── adding_targets.md
│   └── evaluations/          # dated evaluation rounds
├── seeds/approved_drugs.smi  # curated approved-drug seed corpus
├── targets/<name>/
│   ├── protein.pdbqt         # prepared receptor (generated, not committed)
│   ├── reference_ligands/    # optional known binders, validation only
│   └── config.yaml           # box + pocket, both ligand-free
├── tools/                    # installers, target prep, environment check
├── config.yaml               # global configuration defaults
├── biofuzz-fuzz              # entry points; they put the checkout on sys.path
└── biofuzz-triage
```

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

## Coverage: what counts as new

Two maps, because they answer different questions.

The **union map** has one byte per pocket contact and is never reset. Touching a
residue no pose has touched is the direct analog of AFL++ finding a new edge, and
it is the signal that honestly decays as a pocket fills. A **combination map**
hashes the whole fingerprint to one slot, tracking distinct binding modes under a
two-epoch window; alone it is a poor signal, because nearly every distinct pose
hashes somewhere unseen, so it serves as the weaker of the two. Novelty is
`strong` on a new union bit, `weak` on an unseen combination, `none` otherwise.

`union_coverage()` is the number worth watching. Combination-map occupancy stays
near zero at CPU docking speeds by construction.

Coverage is contact-level, not energetic, and interaction typing is a heavy-atom
approximation without donor–acceptor geometry. Two chemically different poses
touching the same residues look identical to the map.

---

## Where the oracle's thresholds come from

There is no per-target threshold calibration. The hit gate must not be anchored
to a known inhibitor, because the whole premise is finding a binder for a protein
you have no drug for — a gate tuned to the answer both defeats that premise and
inflates apparent performance on the bundled example targets. Every target
inherits the one reference-free oracle in `config.yaml`. The tiers themselves are
tabulated in the [README](../README.md).

The known inhibitors under `targets/<name>/reference_ligands/` remain an optional
**validation** set. Docking them by hand answers one question: is the gate so
strict it would reject a real drug?

Ranges below are from **three replicate runs** at the in-loop docking settings
(exhaustiveness 8, `--cnn fast`, 3 modes) on a CPU-only host. Replication matters
here: a single run reads like a precise measurement, and the CNN pose column in
particular is not.

| Target | Reference | vina | CNN | consensus | LE | CNN pose |
|---|---|---|---|---|---|---|
| hiv_protease | Indinavir | −12.09..−12.06 | −13.18..−13.16 | **−12.09..−12.06** | 0.268–0.269 | 0.941–0.942 |
| braf_v600e | Vemurafenib | −11.46..−11.45 | −12.52..−12.49 | **−11.46..−11.45** | 0.347 | 0.961–0.963 |
| parp1 | Talazoparib | −11.91..−11.89 | −10.52..−10.35 | **−10.52..−10.35** | 0.370–0.376 | 0.913–0.963 |
| sars_cov2_mpro | Nirmatrelvir | −8.75..−8.68 | −11.32..−11.18 | **−8.75..−8.68** | 0.248–0.250 | 0.958–0.981 |
| egfr_kinase | Erlotinib | −7.26..−7.14 | −9.77..−9.56 | **−7.26..−7.14** | 0.246–0.250 | 0.598–0.731 |

Only the first and fourth of those targets ship in the repository. The other
three were measured when all five were bundled, and their specs remain in
`tools/prepare_target_fixture.py`, so the table can be reproduced with one
command per target.

Read three things off it.

It sets the ligand-efficiency floor: 0.22 rather than the textbook 0.3, which
would reject erlotinib, indinavir and nirmatrelvir. It sets the pose-confidence
floor: all five clear 0.4 comfortably, and the weakest reading anywhere is
erlotinib's 0.598. And it bounds the affinity tier's honesty — at −9.0,
**hiv_protease, braf_v600e and parp1 pass; sars_cov2_mpro and egfr_kinase do
not**, in all three replicates. That is the price of refusing a per-target
threshold.

Note also that `consensus` tracks vina on four of five targets and the CNN on
parp1, which is the policy working as intended: whichever function is less
convinced sets the score.

These are docking scores, in a different unit from any published Kd. Even for
validation, dock the molecule through this pipeline rather than comparing against
a literature affinity.

---

## Output layout

```
runs/<date>_<target>/
├── corpus/state.json     # corpus checkpoint (resume reads this)
├── coverage.json         # union + combination maps, novelty counts
├── essential.json        # emergent essential-residue tallies
├── findings/
│   ├── counter.json
│   └── 000001_<ts>_<affinity>/{pose.pdbqt, metadata.json}
├── cache/                # prepared PDBQTs, keyed by canonical SMILES
├── fuzzer.log            # always written, status screen or not
└── triage/               # written by biofuzz-triage
    ├── report.json
    ├── report.html
    └── top_hits/001_<hash>/{pose.pdbqt, metadata.json, summary.txt}
```

Each finding's `metadata.json` records why it was called a hit — every tier it
passed, both scoring functions separately, ligand efficiency, CNN pose score,
essential contacts, novelty class, and the mutation that produced it.

Both checkpoint stores carry a schema version and **reject** a mismatch rather
than reinterpreting the fields, because a campaign that looks resumed but is
scheduling on garbage is worse than one that refuses to start. A run directory
name collides only by date and target, so a same-day rerun gets a `_2` suffix.

The PDBQT cache has no automatic invalidation. If preparation parameters or the
Meeko version change between runs, delete `runs/<stamp>/cache/` before resuming.

---

## Configuration

Global defaults live in `config.yaml`; per-target overrides in
`targets/<name>/config.yaml`. The `docking`, `oracle` and `molecules` sections
are merged key by key, so a target restates only what it changes. Every value is
commented in place with the reasoning behind it. All configuration is YAML; no
configuration file is executable Python.

The **docking engine** is selected by name and resolved through
`biofuzz.docker.get_backend`, and the **UI** by name in the CLI, so either can be
swapped without touching the fuzzer. **Preparation is not pluggable by config**:
it is a single implementation behind the `prepare_smiles` function contract, and
swapping it means substituting that function.

---

## Key design decisions

**gnina only.** The docking backend defaults to gnina because its CNN scoring
function adds an independent estimate of both affinity and pose plausibility that
vina's empirical function cannot provide. The backend interface is abstract and
engines resolve by name, but gnina is the only bundled implementation, and there
is no vina fallback.

Choosing gnina only pays off if the CNN columns are actually read. Scoring policy
lives in `oracle/scoring.py` and defaults to `consensus`: vina's score and the
CNN's prediction are put in the same units, and the **weaker** of the two is what
the oracle gates on. Running gnina and then ranking on vina's number alone would
pay CNN inference cost on every dock for nothing.

**No seed triage, but seeds are calibrated.** Seeds are approved drugs, trusted
without verification, so they enter the corpus directly at startup with no
blocking pre-docking phase — as in AFL++, where seeds go straight into the queue.

"No triage phase" is not "never docked". AFL++ calibrates each seed on its first
execution, and so does BioFuzz: the first time an entry is popped, the molecule
itself is docked before any of its mutants. This is what makes repurposing
possible at all. Without it the loop only ever docks mutants, the question "does
this approved drug bind this target?" is never asked, and the power schedule
ranks every seed on a null affinity.

**The oracle never consults a known inhibitor.** Nothing in the hit gate may be
derived from a drug you already have, because the premise is finding binders for
a protein you have none for, and a gate tuned to the answer inflates apparent
performance on exactly the bundled example targets. The accepted cost is that a
single absolute affinity cutoff is coarse without a per-target anchor; the
reference-free quality tiers do the real discriminating, and per-target ranking
belongs in triage.

**Boxes and pockets are ligand-free.** Both come from a pocket detector (P2Rank)
run on the apo receptor, not from residues near a co-crystallised inhibitor, so
the searched site and the coverage map are properties of the protein. A known
inhibitor is never required to add a target.

**The essential-residue set is derived from the protein alone.** Pocket residues
are ranked by burial and polar character, blended with P2Rank ligandability at
prep time, then unioned with the residues that confident seed poses converge on
as the campaign runs. The tier asks where a molecule binds rather than how
tightly, which no other tier does. It is kept tolerant, engage one residue, and
self-disables when no trustworthy set can be formed, because it is a burial
heuristic rather than a catalytic-site predictor.

**Triage is post-fuzzing.** Confirmation re-docking, ADMET filters, pose-quality
analysis, PAINS screening and binding-mode clustering all belong to
`biofuzz-triage`. The in-loop oracle only compares numbers already present in a
single docking result.

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
