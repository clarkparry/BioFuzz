# BioFuzz

**Coverage-guided fuzzing applied to molecular docking.** BioFuzz treats a
protein as a program under test, a small molecule as an input, and a docking run
as one execution. It keeps a prioritised corpus of molecules, mutates them with
staged chemical operators, scores each docked pose, and saves the ones that clear
a hit gate — the same feedback loop a code fuzzer uses to find crashes, pointed
at a binding pocket instead of a parser.

The design follows [AFL++](https://github.com/AFLplusplus/AFLplusplus) closely and
deliberately: a persistent queue with a power schedule, a coverage bitmap that
decides what counts as novel, deterministic → splice → havoc mutation stages, and
a strict split between a fast in-loop oracle and slow offline triage. Where the
analogy stops holding is documented rather than glossed over — see
[Honest limitations](#honest-limitations).

## What it is for

BioFuzz is a **hypothesis generator**, not a virtual-screening replacement and not
a drug-discovery pipeline. It is cheap brute-force breadth: point it at a pocket
and it will explore chemical space around known drugs, reporting molecules that
dock well and engage the site. Deciding whether any of those are real is the job
of the layer after it — an ML model, a medicinal chemist, an assay.

Two use cases motivate it:

- **Repurposing.** Every approved-drug seed is docked against your target on its
  first selection, so the question "does this existing drug bind this protein?"
  is answered for all 250 seeds as a side effect of running a campaign.
- **Lead generation.** Mutants grow outward from validated, synthesisable,
  bioavailable scaffolds rather than from a random corner of chemical space.

## The mapping

| AFL++ | BioFuzz |
|---|---|
| Target binary | Protein (receptor PDBQT + docking box) |
| Input | Molecule (SMILES → PDBQT) |
| Execution | One docking run (gnina) |
| Edge coverage bitmap | Residue-contact fingerprint map |
| Crash | Docking hit that clears every oracle tier |
| Seed corpus | 250 curated approved small-molecule drugs |
| Calibration exec | Docking a corpus entry itself, before its mutants |
| Deterministic stage | Systematic atom / substituent / halogen scans |
| Splice stage | Scaffold and fragment crossover between two entries |
| Havoc stage | Stochastic multi-operation chemical mutation |
| Power schedule | Novelty, affinity and rarity weighted budget |
| Crash triage (CASR) | `biofuzz-triage` |

## Requirements

- Linux x86_64, because the bundled gnina build is a prebuilt Linux binary
- Python 3.12 or newer
- Java 11 or newer, **only** if you want to prepare new targets (P2Rank needs it)
- No GPU required. gnina runs CNN inference on CPU, just slowly — budget tens of
  seconds per dock rather than a few

## Setup

```sh
git clone https://github.com/clarkparry/BioFuzz.git
cd BioFuzz

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # or: pip install -r requirements.txt
```

Two binaries are downloaded rather than vendored, into `.tools/` (gitignored):

```sh
python tools/install_gnina.py        # docking engine -> .tools/bin/gnina
python tools/install_p2rank.py       # pocket detector -> .tools/p2rank/
```

The five bundled targets ship their docking box and pocket residues in git, but
their prepared receptors are generated, so build those once:

```sh
python tools/prepare_target_fixture.py        # all five, fetches from RCSB
```

Then confirm the machine can actually run a campaign:

```sh
python tools/check_environment.py
```

```
Dependencies
  [ok  ] Python >= 3.12  3.12.3 at /home/you/BioFuzz/.venv/bin/python
  [ok  ] PyYAML  6.0.3
  [ok  ] RDKit  2025.09.6
  [ok  ] Meeko  0.7.1
  [ok  ] gnina  gnina v1.3.2 master:f23dd2b  (/home/you/BioFuzz/.tools/bin/gnina)
  [ok  ] P2Rank  /home/you/BioFuzz/.tools/p2rank/prank

Targets
  [ok  ] hiv_protease  receptor 143 KiB
  ...

Ready to fuzz. Try: ./biofuzz-fuzz --target hiv_protease --max-iterations 1
```

If gnina exits non-zero here, it is almost always a missing CUDA runtime: the
prebuilt binary is CUDA-linked even when it runs on CPU. On a GPU-less host,
`tools/patch_gnina_cpu_runtime.sh` installs the runtime libraries as pip wheels
and repoints the binary's rpath at them.

## Usage

### Run a campaign

```sh
./biofuzz-fuzz --target hiv_protease
```

Runs until you interrupt it, checkpointing every 500 iterations or 5 minutes,
whichever comes first. On a TTY you get a live AFL-style status screen; piped,
it falls back to quiet output.

A short run, useful for checking the whole loop end to end:

```sh
./biofuzz-fuzz --target hiv_protease --max-iterations 2 --workers 4 --seed 11
```

```
[HIT] CC1(C)S[C@@H]2[C@H](CC(=O)[C@H](N)c3ccc(O)c(C(F)(F)F)c3)C(=O)N2[C@H]1C(=O)O | affinity=-9.04 LE=0.31
[HIT] CC1(C)S[C@@H]2[C@H](CC(=O)[C@H](N)c3ccc(O)cc3N)C(=O)N2[C@H]1C(=O)O | affinity=-9.00 LE=0.35
[HIT] CC1(C)S[C@@H]2[C@H](CC(=O)[C@H](N)c3ccc(O)c(C#N)c3)C(=O)N2[C@H]1C(=O)O | affinity=-9.02 LE=0.33
Campaign complete: max_iterations | iterations=2 hits=3 best_affinity=-9.04 checkpoints=2
```

That took about 11 minutes and 57 docks on 4 CPU workers. The three hits are all
derivatives of one seed, amoxicillin, carrying an added trifluoromethyl, amine or
nitrile — and all three also have the side-chain amide swapped for a ketone,
which is the deterministic stage's atom scan at work. Whether that trade is
worth making is a question for a chemist, which is the point: these are
hypotheses, and the honest reading of the output is "three analogs of one
penicillin scaffold scored at the gate", not "three leads".

`--seed` fixes BioFuzz's own stage and donor selection. It does **not** make the
run bit-reproducible: gnina seeds its search independently, so scores move a
little between runs.

### Resume an interrupted campaign

```sh
./biofuzz-fuzz --target hiv_protease --resume runs/2026-09-11_hiv_protease
```

Corpus, coverage maps, findings and the emergent essential-residue set all come
back. Seeds already calibrated are not re-docked.

### Triage the findings

```sh
./biofuzz-triage --findings runs/2026-09-11_hiv_protease/findings --target hiv_protease
```

```
Triage complete: 3 findings analyzed, 0 filtered, report at runs/2026-09-11_hiv_protease/triage/report.json
```

Triage re-docks each finding at higher exhaustiveness, then adds pose quality,
ligand efficiency, ADMET flags, PAINS and reactive-group screens, optional
off-target selectivity, and binding-mode clustering. It writes `report.json`,
a browsable `report.html`, and `top_hits/` with a pose and summary per ranked
candidate.

### Other useful flags

```sh
./biofuzz-fuzz --target parp1 --workers 8 --ui json | jq .        # machine-readable status
./biofuzz-fuzz --target parp1 --seeds my_actives.smi              # your own seed corpus
./biofuzz-fuzz --target parp1 --config alt.yaml                   # alternative global config
./biofuzz-triage --findings <dir> --target parp1 --top-n 50
```

Full flag lists are in `--help` on both tools.

## How a campaign works

```
seeds/approved_drugs.smi ─→ Corpus (no pre-docking; priors from drug-likeness
        │                            and scaffold diversity)
        ▼
   Corpus.pop()  ────────── power schedule picks the entry and its budget
        │
        ├─ first selection? dock the entry itself (calibration)
        ▼
   Mutator ──────────────── deterministic → splice → havoc
        │
        ▼
   Prep ────────────────── SMILES → 3D → PDBQT, property + stability gate
        │                   (failures are discarded, never patched up)
        ▼
   Docking ─────────────── gnina, parallel across workers
        │
        ├──→ Coverage ──── contact fingerprint → novelty class + rarity
        │
        ├──→ Oracle ────── six tiers → hit? → save finding
        │
        └──→ Corpus.add()  requeue with the evidence just gathered
```

### Coverage: what counts as new

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

### The oracle: six tiers, no known inhibitor

A molecule is a hit only if it clears every enabled tier. The gate is
**reference-free** on purpose: BioFuzz is meant to find binders for a protein you
have no drug for, so nothing in it may be derived from a drug you already have.

| Tier | Check | Default |
|---|---|---|
| Affinity | consensus score ≤ threshold | −9.0 kcal/mol |
| Strain | intramolecular energy ≤ threshold | 3.5 kcal/mol |
| Ligand efficiency | −ΔG / heavy atoms ≥ floor | 0.22 |
| Pose confidence | gnina CNN pose score ≥ floor | 0.40 |
| Score agreement | \|vina − CNN\| ≤ maximum | off |
| Essential contacts | pose engages ≥ N anchor residues | 1 |

Tiers skip rather than fail when their input is unavailable, so a missing CNN
column or an undetectable pocket cannot silently reject everything.

"Consensus" is the key scoring choice. gnina reports two independent estimates
per pose, vina's empirical score and a CNN-predicted pKd. BioFuzz converts the
CNN prediction to kcal/mol and gates on **the weaker of the two**, so a molecule
has to convince both functions. Each function's false positives are largely the
other's rejects.

The essential-contact tier is the only one that asks *where* a molecule binds
rather than how tightly. Its residue set is derived from the protein alone: rank
pocket residues by burial and polar character, blend in P2Rank ligandability
where available, then union in the residues that confident seed poses actually
converge on as the campaign runs. On the bundled HIV protease receptor, treated
as an unknown protein, the top two residues come out as the catalytic aspartate
dyad (A:25 / B:25) without either ever being named.

### Reference drugs, measured through this pipeline

The known inhibitors under `targets/<name>/reference_ligands/` are a
**validation** set, never an input to discovery. Docking them by hand answers one
question: is the gate so strict it would reject a real drug? Ranges below are
from three replicate runs at the in-loop docking settings on a CPU-only host.

| Target | Reference drug | Consensus (kcal/mol) | Ligand efficiency | CNN pose |
|---|---|---|---|---|
| hiv_protease | Indinavir | −12.09 to −12.06 | 0.268–0.269 | 0.941–0.942 |
| braf_v600e | Vemurafenib | −11.46 to −11.45 | 0.347 | 0.961–0.963 |
| parp1 | Talazoparib | −10.52 to −10.35 | 0.370–0.376 | 0.913–0.963 |
| sars_cov2_mpro | Nirmatrelvir | −8.75 to −8.68 | 0.248–0.250 | 0.958–0.981 |
| egfr_kinase | Erlotinib | −7.26 to −7.14 | 0.246–0.250 | 0.598–0.731 |

Two things this table is used for, and one thing it shows that is unflattering.

It sets the ligand-efficiency floor: the textbook 0.3 would reject three of these
five approved drugs, so the default is 0.22. It sets the pose-confidence floor:
the weakest reading across all replicates is erlotinib's 0.598, so 0.40 admits
every one of them with room to spare.

And it shows the cost of a single global affinity cutoff. **Three of the five
reference drugs clear −9.0; erlotinib and nirmatrelvir do not.** That is the
price of refusing a per-target threshold, and it is a real limitation rather than
a rounding error. A per-target gate would fix it and would also anchor the gate
to the answer, which is the thing this design will not do. If you want a
per-target notion of "good", rank hits in triage instead of retuning the gate.

These are docking scores in a different unit from any published K<sub>d</sub>.
Even for validation, dock the molecule through this pipeline rather than
comparing against a literature affinity.

## Bundled targets

| Target | Protein | PDB | Notes |
|---|---|---|---|
| `hiv_protease` | HIV-1 protease | 1HSG | Hand-curated config; site spans the A/B dimer interface |
| `egfr_kinase` | EGFR kinase domain | 1M17 | |
| `parp1` | PARP1 catalytic domain | 7KK3 | |
| `sars_cov2_mpro` | SARS-CoV-2 main protease | 7VLP | Wild type, 1.50 Å |
| `braf_v600e` | BRAF V600E | 3OG7 | Raises `max_logp` to admit type-II inhibitors |

Boxes and pocket residues come from P2Rank run on the apo receptor, not from a
co-crystallised ligand, so the searched site is a property of the protein. The
one exception is `hiv_protease`, whose active site sits at the dimer interface
behind mobile flaps — a rigid-pocket detector undershoots it, so that config is
hand-curated and marked to survive a rebuild. Adding your own target is one
`TargetSpec` entry and one command; see
[docs/adding_targets.md](docs/adding_targets.md).

Two of the five need `molecules:` overrides, and the reason generalises: the
global 550 Da / logP 5.0 envelope excludes indinavir (614 Da) and vemurafenib
(logP 5.54), and with them the entire chemical class that works on those targets.
Molecules outside the bounds are dropped at preparation, before docking. **Check
that a new target's bounds admit its own chemistry**, or no campaign against it
can find anything resembling what works.

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
├── fuzzer.log            # always written, TUI or not
└── triage/               # written by biofuzz-triage
    ├── report.json
    ├── report.html
    └── top_hits/001_<hash>/{pose.pdbqt, metadata.json, summary.txt}
```

Each finding's `metadata.json` records why it was called a hit — every tier it
passed, both scoring functions separately, ligand efficiency, CNN pose score,
essential contacts, novelty class, and the mutation that produced it.

The PDBQT cache has no automatic invalidation. If preparation parameters or the
Meeko version change between runs, delete `runs/<stamp>/cache/` before resuming.

## Configuration

`config.yaml` holds global defaults; `targets/<name>/config.yaml` overrides them
per target. The `docking`, `oracle`, `triage` and `molecules` sections are merged
key by key, so a target restates only what it changes. Every value is commented
in place with the reasoning behind it.

The docking engine and the UI are selected by name in config. Preparation is a
single implementation behind a function contract, not a registry.

## Honest limitations

These are design constraints, not a to-do list.

**The oracle is weak, and that is the load-bearing caveat.** A code fuzzer's
oracle is near-perfect: a segfault is a segfault. A docking oracle is a scoring
function with known, substantial error, so BioFuzz's "hits" are hypotheses of
genuinely uncertain quality. Everything about the design follows from taking that
seriously — the consensus policy, the multi-tier gate, post-hoc triage, and the
framing of this tool as a precursor to expert or model-driven triage rather than
a replacement for it.

**One global affinity threshold does not fit every target.** Loose on
tight-binding sites, strict on weak ones; two of five reference drugs miss it.
Accepted as the cost of a reference-free gate.

**Docking treats the receptor as rigid.** No induced fit, no explicit waters, no
protonation-state search. Pockets that reorganise on binding are poorly served.

**The essential-residue set encodes a hypothesis about *the* binding mode.** It
is a mild bias against novel or allosteric modes. The gate is kept tolerant
(engage one residue) and self-disabling to bound that cost. It is a burial and
polarity heuristic, not a catalytic-site predictor: on SARS-CoV-2 Mpro it ranks
His41 fourth and does not surface Cys145 at all.

**Coverage is contact-level, not energetic.** Two chemically different poses
touching the same residues look identical to the map.

**The bioisostere library is a representative subset**, and splice-stage donor
selection is uniform random rather than novelty-weighted.

**Favored-entry reassignment is not implemented.** `favored` is a sticky flag:
the first pioneer of a coverage slot keeps it, and a later, better entry does not
inherit it.

**Selectivity has never run against a real off-target.** No bundled target
configures `offtarget_receptor`, so that triage stage has only been exercised in
its skip path.

## Development

```sh
pytest                                  # 183 tests, ~3 min
pytest tests/test_oracle.py -q          # one module
pytest -k "not docker and not coverage" # skip the live-docking tests
```

The suite is deliberately slow because several tests call gnina for real rather
than mocking the docking path — including a full prepare → dock → parse →
fingerprint → observe integration test. Those are the tests that have historically
caught the defects unit tests missed, so they earn their runtime.

Repository layout:

```
biofuzz/
├── cli/          # biofuzz-fuzz and biofuzz-triage entry points
├── corpus/       # prioritised queue, power schedule, scaffold crowding
├── coverage/     # contact fingerprints, union + combination maps
├── docker/       # docking backend contract and gnina implementation
├── fuzzer/       # campaign orchestration, config merging, workers
├── mutator/      # three mutation stages, structural alerts, filters
├── oracle/       # in-loop hit gating and scoring policy
├── prep/         # SMILES → 3D → PDBQT
├── protein/      # receptor parsing, essential-residue derivation
├── seeds/        # seed loading and priors
├── storage/      # findings, checkpoints, PDBQT cache, run layout
├── triage/       # post-campaign analysis stages and report
└── ui/           # terminal TUI plus json / quiet / none
docs/             # architecture, per-module design, evaluations
tools/            # installers, target preparation, environment check
targets/<name>/   # receptor, box, pocket residues per target
seeds/            # curated approved-drug seed corpus
```

Design documentation lives in [docs/architecture.md](docs/architecture.md) for
the overview and [docs/modules/](docs/modules/) for one document per module.
Dated evaluation rounds are in [docs/evaluations/](docs/evaluations/); the
protocol and pass criteria are committed before each run and the results are
published either way.

## Third-party components

None of these is redistributed. The two binaries are downloaded by
`tools/install_*.py` at setup time and invoked as subprocesses; the two Python
libraries are ordinary dependencies.

| Component | Role | License |
|---|---|---|
| [gnina](https://github.com/gnina/gnina) | docking with CNN rescoring | Apache-2.0 |
| [P2Rank](https://github.com/rdk/p2rank) | ligand-free pocket detection | MIT |
| [RDKit](https://www.rdkit.org/) | cheminformatics | BSD-3-Clause |
| [Meeko](https://github.com/forlilab/Meeko) | PDBQT preparation | LGPL-2.1 |

Receptor structures are fetched from the [RCSB PDB](https://www.rcsb.org) at
target-preparation time and are not committed.

The seed corpus in `seeds/approved_drugs.smi` is derived from the
861-compound approved-drug set (`data/external_library.csv`) published by the
[AiCures / coronavirus_data](https://github.com/yangkevin2/coronavirus_data)
project, canonicalised and filtered to 250 entries by `tools/curate_seeds.py`.
**That repository declares no license.** What is reproduced here is a set of
canonical SMILES for approved drugs — structural facts, not authored content —
but if you need a cleanly licensed provenance chain, regenerate the file from a
source whose terms you have checked. `tools/curate_seeds.py` takes any
one-SMILES-per-line input.

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE).
