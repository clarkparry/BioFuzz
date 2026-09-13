# BioFuzz

**Coverage-guided fuzzing applied to molecular docking.** BioFuzz treats a
protein as a program under test, a small molecule as an input, and a docking run
as one execution. It keeps a prioritised corpus of molecules, mutates them with
staged chemical operators, scores each docked pose, and saves the ones that clear
a hit gate — the same feedback loop a code fuzzer uses to find crashes, pointed
at a binding pocket instead of a parser.

This is a **proof of concept**, not a product: two bundled targets, one docking
engine, and an honest account of where the analogy stops holding. The design
follows [AFL++](https://github.com/AFLplusplus/AFLplusplus) deliberately, with a
persistent queue and power schedule, a coverage map that decides what counts as
novel, deterministic → splice → havoc mutation stages, and a strict split
between a fast in-loop oracle and slow offline triage.

BioFuzz is a **hypothesis generator**, not a virtual-screening replacement and
not a drug-discovery pipeline. Point it at a pocket and it explores chemical
space around known drugs, reporting molecules that dock well and engage the
site. Deciding whether any of them are real is the job of the layer after it: an
ML model, a medicinal chemist, an assay. Every approved-drug seed is also docked
against your target on its first selection, so "does this existing drug bind
this protein?" is answered for all 250 seeds as a side effect of a campaign.

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
pip install -e ".[dev]"
```

Two binaries are downloaded rather than vendored, into `.tools/` (gitignored):

```sh
python tools/install_gnina.py        # docking engine -> .tools/bin/gnina
python tools/install_p2rank.py       # pocket detector -> .tools/p2rank/
```

Both bundled targets ship their docking box and pocket residues in git, but
their prepared receptors are generated, so build those once, then check the
machine can actually run a campaign:

```sh
python tools/prepare_target_fixture.py     # fetches from RCSB
python tools/check_environment.py
```

```
Dependencies
  [ok  ] Python >= 3.12  3.12.3 at /home/you/BioFuzz/.venv/bin/python
  [ok  ] RDKit  2025.09.6
  [ok  ] gnina  gnina v1.3.2 master:f23dd2b  (/home/you/BioFuzz/.tools/bin/gnina)
  [ok  ] P2Rank  /home/you/BioFuzz/.tools/p2rank/prank

Targets
  [ok  ] hiv_protease  receptor 143 KiB

Ready to fuzz. Try: ./biofuzz-fuzz --target hiv_protease --max-iterations 1
```

If gnina exits non-zero here, it is almost always a missing CUDA runtime: the
prebuilt binary is CUDA-linked even when it runs on CPU. On a GPU-less host,
`tools/patch_gnina_cpu_runtime.sh` installs the runtime libraries as pip wheels
and repoints the binary's rpath at them.

## Usage

```sh
./biofuzz-fuzz --target hiv_protease
```

Runs until you interrupt it, checkpointing every 500 iterations or 5 minutes,
whichever comes first. On a TTY you get a live AFL-style status screen; piped, it
prints only hits. A short run, useful for checking the loop end to end:

```sh
./biofuzz-fuzz --target hiv_protease --max-iterations 2 --workers 4 --seed 11
```

```
[HIT] CC1(C)S[C@@H]2[C@H](CC(=O)[C@H](N)c3ccc(O)c(C(F)(F)F)c3)C(=O)N2[C@H]1C(=O)O | affinity=-9.04 LE=0.31
[HIT] CC1(C)S[C@@H]2[C@H](CC(=O)[C@H](N)c3ccc(O)cc3N)C(=O)N2[C@H]1C(=O)O | affinity=-9.00 LE=0.35
[HIT] CC1(C)S[C@@H]2[C@H](CC(=O)[C@H](N)c3ccc(O)c(C#N)c3)C(=O)N2[C@H]1C(=O)O | affinity=-9.02 LE=0.33
Campaign complete: max_iterations | iterations=2 hits=3 best_affinity=-9.04 checkpoints=2
```

That took about 11 minutes and 57 docks on 4 CPU workers. All three hits are
derivatives of one seed, amoxicillin. The honest reading is "three analogs of
one penicillin scaffold scored at the gate", not "three leads". `--seed` fixes
stage and donor selection but not the run, because gnina seeds its search
independently.

Resume an interrupted campaign, then triage what it found:

```sh
./biofuzz-fuzz --target hiv_protease --resume runs/2026-09-11_hiv_protease
./biofuzz-triage --findings runs/2026-09-11_hiv_protease/findings --target hiv_protease
```

Corpus, coverage maps, findings and the emergent essential-residue set all come
back on resume, and seeds already calibrated are not re-docked. Triage re-docks
each finding at higher exhaustiveness, then adds pose quality, ligand
efficiency, ADMET flags, PAINS and reactive-group screens, and binding-mode
clustering. It writes `report.json`, a browsable `report.html`, and `top_hits/`
with a pose and summary per ranked candidate.

Other flags worth knowing, with the full list in `--help` on both tools:

```sh
./biofuzz-fuzz --target sars_cov2_mpro --workers 8 --ui quiet
./biofuzz-fuzz --target sars_cov2_mpro --seeds my_actives.smi    # your own corpus
./biofuzz-triage --findings <dir> --target sars_cov2_mpro --top-n 50
```

## The oracle: six tiers, no known inhibitor

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
rather than how tightly. Its residue set comes from the protein alone: pocket
residues ranked by burial and polar character, blended with P2Rank
ligandability, then unioned with the residues confident seed poses converge on.
On the bundled HIV protease receptor, treated as an unknown protein, the top two
come out as the catalytic aspartate dyad without either ever being named.

Where the thresholds come from, measured by docking five approved drugs through
this pipeline, is in [docs/architecture.md](docs/architecture.md).

## Bundled targets

| Target | Protein | PDB | Notes |
|---|---|---|---|
| `hiv_protease` | HIV-1 protease | 1HSG | Hand-curated config; site spans the A/B dimer interface |
| `sars_cov2_mpro` | SARS-CoV-2 main protease | 7VLP | Wild type, 1.50 Å; box and pocket from P2Rank |

Two targets ship, because this is a demonstration rather than a screening
library. Boxes and pocket residues come from P2Rank run on the apo receptor, so
the searched site is a property of the protein. HIV protease is the exception:
its site sits at the dimer interface behind mobile flaps, which a rigid-pocket
detector undershoots, so that config is hand-curated. The fixture builder also
carries specs for EGFR kinase, PARP1 and BRAF V600E, so the five-drug
calibration set can be rebuilt with one command each. Adding your own target is
one `TargetSpec` entry and one command; see
[docs/adding_targets.md](docs/adding_targets.md).

HIV protease needs a `molecules:` override, and the reason generalises: the
global 550 Da envelope excludes indinavir at 614 Da, and with it the entire
peptidomimetic class that works on that protein. Molecules outside the bounds
are dropped at preparation, before docking. **Check that a new target's bounds
admit its own chemistry**, or no campaign against it can find anything
resembling what works.

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
tight-binding sites, strict on weak ones; two of the five drugs measured for
calibration miss it. Accepted as the cost of a reference-free gate.

**Docking treats the receptor as rigid.** No induced fit, no explicit waters, no
protonation-state search. Pockets that reorganise on binding are poorly served.

**The essential-residue set encodes a hypothesis about *the* binding mode**, so
it is a mild bias against novel or allosteric modes. It is a burial and polarity
heuristic, not a catalytic-site predictor: on SARS-CoV-2 Mpro it ranks His41
fourth and does not surface Cys145 at all. The tier stays tolerant and
self-disabling to bound that cost.

**Coverage is contact-level, not energetic.** Two chemically different poses
touching the same residues look identical to the map.

**The bioisostere library is a representative subset**, splice-stage donor
selection is uniform random rather than novelty-weighted, and favored-entry
reassignment is not implemented: the first pioneer of a coverage slot keeps the
flag even when a better entry arrives.

## Development

```sh
pytest                                  # full suite, ~3 min
pytest -k "not docker and not coverage" # skip the live-docking tests, ~2 s
```

Several tests call gnina for real rather than mocking the docking path,
including a full prepare → dock → parse → fingerprint → observe integration
test. Those have historically caught the defects unit tests missed, so they earn
their runtime. Continuous integration runs the fast subset only, since no docking
engine is installed there.

Design notes are in [docs/architecture.md](docs/architecture.md), which covers
module boundaries, data flow, the run directory layout and the reasoning behind
each default. Dated evaluation rounds are in
[docs/evaluations/](docs/evaluations/); the protocol and pass criteria are
committed before each run and the results are published either way.

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
861-compound approved-drug set published by the
[AiCures / coronavirus_data](https://github.com/yangkevin2/coronavirus_data)
project, canonicalised and filtered to 250 entries by `tools/curate_seeds.py`.
**That repository declares no license.** What is reproduced here is canonical
SMILES for approved drugs, structural facts rather than authored content, but if
you need a cleanly licensed provenance chain, regenerate the file from a source
whose terms you have checked. The curation script takes any
one-SMILES-per-line input.

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE).
