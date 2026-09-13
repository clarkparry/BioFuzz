# Changelog

Notable changes to BioFuzz. Dates are when the work landed, not release dates.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely; versioning is [semantic](https://semver.org/) but pre-1.0, so the
interfaces described in `docs/modules/` may still change.

## [0.3.0] — 2026-09-11

Repository prepared for public release, plus correctness fixes surfaced while
verifying the documentation against the code.

### Added
- `pyproject.toml`: the package is installable (`pip install -e .`) and exposes
  `biofuzz-fuzz` and `biofuzz-triage` as console scripts. The repo-root scripts
  of the same names remain as wrappers for running a checkout in place.
- `tools/check_environment.py`, replacing a stale audit script that imported a
  module removed in an earlier rebuild and had been broken since. Verifies
  dependencies, the docking engine, and which targets have a built receptor.
- `AGPL-3.0-or-later` license.

### Fixed
- `docking.engine` in `config.yaml` now actually selects the docking backend.
  The campaign and the worker resolve it through `get_backend()`; previously both
  imported `GninaBackend` directly and the config key only labelled the UI.
- `gpu_active` is reported as `None` rather than `True` when the docking log is
  empty or truncated. gnina announces a missing GPU but says nothing when one is
  present, so presence can only be inferred from silence — and an empty log
  carries no silence to interpret.
- Seed priors now receive the corpus's live scaffold census, so the
  scaffold-diversity bonus discriminates between seeds. Called without it, it
  returned the same constant for every seed and the starting order degenerated to
  file order. Priors are also written to `base_priority` rather than `priority`,
  which the corpus derives and would have overwritten.
- `Corpus.load` rejects a checkpoint whose schema version does not match, as
  `CoverageMap.load` already did and as `docs/modules/storage.md` documented.
- Triage's ligand efficiency uses the oracle's definition (`-dG / heavy atoms`)
  instead of `abs(affinity) / heavy atoms`, which turned an unfavourable positive
  score into a maximally efficient one. The same sign error in the overall-rank
  score is fixed, so an unfavourable finding can no longer outrank a real binder.
- Hit log lines no longer render as `[HIT] [HIT] ...`; the level prefix was
  duplicated between the log formatter and the message.
- The final checkpoint on exit no longer duplicates the one the last iteration
  just wrote.
- `tools/prepare_target_fixture.py` resolves Meeko's `mk_prepare_receptor.py`
  through the running interpreter and PATH instead of assuming a `.venv`
  directory layout.

### Changed
- `.agent/tools/` and `scripts/` consolidated into `tools/`;
  `BIOFUZZ_STRUCTURE.md` moved to `docs/architecture.md`; the dated evaluation
  moved to `docs/evaluations/`.
- Removed `scripts/prep_protein.sh`, which called ADFRsuite's
  `prepare_receptor` and contradicted the documented Meeko-based pipeline.
- `config.yaml` gained a `ui.mode` key. The CLI already read it; it had never
  been present in the file.
- Documentation corrected throughout against the code. The substantive
  corrections: the preparation backend is **not** config-pluggable (only the
  docking engine and UI are); coverage interaction types are a heavy-atom
  approximation without the donor–acceptor geometry the doc described; triage
  selectivity is a ΔΔG, not a ratio; checkpoint schema versions and dataclass
  field lists were stale in several places.
- Reference-drug measurements re-taken in triplicate. This corrected three
  claims: the conventional 0.3 ligand-efficiency floor rejects three of the five
  bundled reference drugs rather than four; their CNN pose scores span
  0.598–0.981 rather than 0.801–0.980; and **three of five clear the -9.0
  affinity gate rather than all five**, which is the honest cost of a single
  reference-free threshold.
- Removed the build journal (`docs/IMPLEMENTATION_LOG.md`) and the separate
  improvements write-up. Their durable content is here and in
  `docs/evaluations/2026-07.md`.

### Testing
- 163 → 183 tests. New coverage for backend selection by config name, GPU
  reporting, checkpoint version rejection, seed-prior discrimination, and the
  triage ligand-efficiency sign convention.

## [0.2.0] — 2026-08

Removed all reliance on a target's known inhibitor, so the hit gate cannot be
anchored to an answer you already have.

### Removed
- Per-target `affinity_threshold` calibrated from where a reference drug docks,
  and the script that calibrated it. Every target inherits one reference-free
  global gate.
- Target-specific seed sets (`seeds/per_target/`) and their priority bonus. No
  seed can be privileged for being the known answer.
- Ligand-derived docking boxes and pocket residue sets.

### Added
- Ligand-free box and pocket detection via P2Rank, run on the apo receptor, so
  the searched site is a property of the protein.
- Essential-residue oracle tier: the only tier that asks *where* a molecule binds
  rather than how tightly. The residue set is derived from the protein alone —
  burial and polar character, blended with P2Rank ligandability, then unioned
  with the residues confident seed poses converge on. On the bundled HIV protease
  receptor it recovers the catalytic aspartate dyad unaided.
- `docs/adding_targets.md`.

## [0.1.0] — 2026-07

First end-to-end evaluation round and the corrections it produced. Full write-up:
[`docs/evaluations/2026-07.md`](docs/evaluations/2026-07.md).

### Fixed
- gnina's CNN pose score and CNN affinity were parsed and discarded, so every
  dock paid CNN inference cost and then ranked on vina's score alone. Added
  `oracle/scoring.py` with a `consensus` default policy.
- Strain was read from a pose REMARK that gnina does not emit, so the tier never
  fired. It is read from the log table's `intramol` column.
- Corpus entries were never docked, only their mutants, so the repurposing
  question was never asked and every seed kept `best_affinity = None`. Added
  `Campaign._calibrate()`.
- Havoc was unreachable, because splice was preferred whenever a donor existed
  and a donor exists whenever the corpus holds more than one molecule.
- Novelty was near-constant: hashing a whole fingerprint to one slot gives no
  partial credit and no new-edge signal. Added a never-reset union map indexed by
  residue, plus a non-saturating rarity term.
- The queue collapsed onto one chemical series. Added a scaffold-crowding
  discount and split priority into intrinsic worth and crowding.
- Seed priority rewarded raw molecular weight without bound, so the heaviest seed
  was always explored first. Replaced with a band centred on 350 Da.
- `molecules:` property bounds were global-only and silently excluded two
  targets' own reference drugs — and with them the entire chemical class that
  works on those targets. Bounds are now per-target overridable.
- A campaign could run for hours without checkpointing, and there was no
  `--resume`. Added a wall-clock checkpoint interval and resume support.
- Findings were not deduplicated, and prepared molecules were re-docked when a
  mutation rediscovered them.
- Triage selectivity divided two kcal/mol energies, which is a unit error. It is
  now a ΔΔG.
- Per-target `docking` overrides were silently discarded by config merging.

### Added
- `mutator/alerts.py`: a narrow structural-alert catalog for motifs that are
  unstable or not isolable. Property bounds say nothing about whether a molecule
  can exist, and 9 of the round's 10 findings carried a hydroxylamine ether or a
  hemiacetal produced by walking an ethoxy chain one atom at a time.
- In-loop ligand-efficiency and CNN pose-confidence oracle tiers.

## [0.0.0] — 2026-04

Ground-up rebuild against `docs/architecture.md` and `docs/modules/`. Ten
modules — preparation, docking backend, coverage, oracle, corpus, mutator,
fuzzer, seeds, UI, triage — behind two CLI entry points.
