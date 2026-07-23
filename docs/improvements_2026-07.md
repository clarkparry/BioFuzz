# BioFuzz Improvements — July 2026

> **Superseded in part (post-July-2026).** The known-inhibitor reliance these
> changes still assumed has since been removed: target-specific seeds and their
> priority bonus, the reference-measured per-target `affinity_threshold` (and
> `scripts/calibrate_oracle.py`), and the ligand-derived box/pocket. Boxes and
> pockets are now detected ligand-free with P2Rank and the oracle is
> reference-free. Sections G1/G2/D2 below describing per-target measured
> thresholds or `calibrate_oracle.py` are historical; see
> [`adding_targets.md`](adding_targets.md) and [`modules/oracle.md`](modules/oracle.md).

Changes made in response to [`docs/evaluation_2026-07.md`](evaluation_2026-07.md).
Section letters below match the finding they address.

Tests: 92 → **147**, all passing. Suite runtime ~6m40s → ~4m (the docking tests
were running gnina's full CNN ensemble at a 120s timeout — ~97s per dock on an
idle CPU here, so they flaked under parallel load; they now use the `fast` model
the fuzzer actually runs).

---

## Scoring

### A1 — gnina's CNN scores are now used (`docker/parser.py`, `oracle/scoring.py`)

`parse_log` now returns every column: `affinity`, `intramol`, `cnn_pose_score`,
`cnn_affinity`. It also handles gnina's *other* table shape — with
`--cnn_scoring=none` the layout is vina's 4-column `affinity | rmsd l.b. | rmsd
u.b.`, which the old 5-float-mandatory regex failed to parse at all.

New `biofuzz/oracle/scoring.py` makes the two scoring functions comparable and
combinable:

- `pkd_to_kcal()` — the CNN predicts pKd (higher = tighter); vina reports kcal/mol
  (lower = tighter). Converted via ΔG = -1.364 × pKd (RT·ln10 at 298 K) so
  everything downstream speaks one unit.
- `score_mode(mode, policy)` — `vina` | `cnn` | `consensus`.
- `best_mode(modes, policy)` — re-selects the best pose *under the active policy*.
  gnina sorts by whichever function it was told to rank with, which need not be
  the one we gate on.

**Default policy: `consensus`** — the weaker (least negative) of vina and the CNN.
A molecule must convince both functions. Each function's false positives are
largely the other's rejects, and requiring agreement is what stops single-function
noise from becoming a finding. `vina` reproduces the old behaviour exactly.

### A2 — the strain tier works (`oracle/oracle.py`)

Strain now comes from the `intramol` column, with the old pose-REMARK regex kept
as a fallback for engines that do annotate poses. Measured reference-drug strain
is -1.18 to +0.60 kcal/mol, comfortably inside the 3.5 threshold — so the tier
now discriminates instead of silently passing everything.

### A3 — ligand efficiency gates *in the loop* (`oracle/oracle.py`)

New `min_ligand_efficiency` tier (LE = -ΔG / heavy atoms), evaluated during
fuzzing rather than only in triage. This is what stops the campaign climbing the
molecular-weight gradient with its whole budget.

**Calibrated, not guessed.** The textbook 0.3 floor rejects four of five
reference drugs (see G2). The default is **0.22**, just below nirmatrelvir's
0.247. The tier is skipped — not failed — when no structure is available, so
callers without SMILES still work.

### New — CNN pose-score tier

`min_cnn_pose_score` (default **0.4**) gates on gnina's confidence that the pose
is a real binding mode. This turns out to be the sharpest discriminator
available: all five reference drugs score **0.801–0.980**, while all ten of the
reference campaign's findings score **0.108–0.314** (measured by re-docking them).
The two populations separate completely at 0.4.

Also added `max_score_disagreement` (off by default) to reject poses where vina
and the CNN conflict by more than N kcal/mol.

`OracleVerdict` now carries `ligand_efficiency`, `heavy_atom_count`,
`vina_affinity`, `cnn_affinity_kcal`, `cnn_pose_score` and `scoring_policy`, and
all of it is written into finding metadata — so a finding records *why* it was
called, per function.

---

## Scheduler

### B1 — havoc is reachable (`corpus/scheduler.py`)

New `select_stage()`: deterministic on first selection, then **~75% havoc / ~25%
splice**, matching AFL++'s balance. Splice is skipped when no donor exists.
Tested directly (`test_havoc_is_reachable_and_dominant_after_first_selection`).

### B2 — mode collapse (`corpus/scheduler.py`, `corpus/corpus.py`)

Three changes:

1. **Scaffold-crowding penalty.** `weight * log2(1 + n)` over entries sharing a
   Bemis-Murcko scaffold, capped at 12.0 so a crowded series is demoted, never
   exiled. Log scaling keeps the first few analogs of a promising series cheap
   while pushing the twentieth behind unexplored chemistry.
2. **Reuse penalty 0.1 → 1.0 per fuzz**, so the queue actually advances.
3. **Rarity bonus.** Mean rarity of the contacts a pose makes, `1/(1+count)`
   per bit. Unlike new-bit novelty this never saturates, so it keeps steering
   toward under-explored corners of the pocket after every residue has been
   touched once.

**Base vs effective priority.** Crowding depends on the rest of the corpus and
changes after an entry is queued, so it cannot live in the stored priority.
`CorpusEntry.base_priority` is intrinsic worth (a seed prior, or evidence once
docked); `priority` is `base - crowding`, derived by `Corpus`.

This split matters more than it looks: a first attempt folded crowding into
`compute_priority` and repriced at pop, which **destroyed every seed's prior** —
an uncalibrated seed has no evidence, so recomputing floored it at 0.1 and threw
away the entire seed ordering. A test now pins that
(`test_seed_priority_survives_entering_the_corpus`).

`pop()` reprices crowding lazily and re-queues, so add() stays O(log n). Heap
records are **versioned** so exactly one is live per molecule — without that,
repricing either loses entries from the heap or duplicates them.

### B3 — corpus entries are docked (`fuzzer/campaign.py`)

New `Campaign._calibrate()`: on first selection, dock the molecule **itself**
before fuzzing its mutants. This is AFL++'s calibration exec — run the input,
learn what it does, then fuzz it.

This is the change that gives BioFuzz a repurposing capability at all. It
directly answers "does this approved drug bind this target?", which the tool
previously never asked. It also gives the power schedule real evidence instead
of `best_affinity = None` for every seed.

Verified in a live run: 30 entries calibrated, 29 with measured affinity, seeds
docked on their own merits.

### B4 — seed MW bias (`seeds/priority.py`)

The MW term is now a **band** centred on 350 Da (`1 - |mw - 350| / 200`) instead
of an unbounded ramp. Small, ligand-efficient seeds can compete. In a live run
the first molecule explored is now a penicillin, not the heaviest seed in the file.

---

## Coverage

### C1 — union bitmap with new-bit novelty (`coverage/bitmap.py`)

Two signals now, mirroring AFL++ properly:

- **Union map** — one byte per pocket contact bit, **never reset**. Setting a
  zero byte means this pose reached a residue no pose had reached before: the
  direct analog of discovering a new edge, and a signal that genuinely decays as
  the pocket fills.
- **Combination map** — the old whole-fingerprint hash, retained as the weaker
  signal where it is meaningful (distinct *binding modes*, not distinct contacts).

```
strong -- reached a contact bit never reached before (new_bits > 0)
weak   -- known contacts, but a binding-mode combination new this epoch
none   -- combination already seen this epoch
```

Only the combination map rotates on epoch. Resetting the union map would make an
explored pocket look novel again.

New `union_coverage()` — fraction of pocket contacts ever reached. This is the
interpretable number the old `bitmap_occupancy` never was: a live run reports
**0.604 (29/48 residues)** where occupancy reports 0.000. Both are now surfaced
in `RuntimeStatus` and the checkpoint log, alongside `distinct_scaffolds` so
mode collapse is visible while a campaign runs rather than only in hindsight.

Novelty is now discriminating: a live run gives strong 8 / weak 18 / none 3,
where the old scheme would have called nearly all of them strong.

---

## Chemistry

### D1 — unstable-motif filter (`mutator/alerts.py`)

A deliberately narrow catalog of motifs that are *unstable or implausible* —
acyclic peroxides, N–O single bonds, acyclic acetals/aminals/thioacetals, gem
diols, acyl halides, heteroatom–halogen bonds, allenes. Wired into
`passes_drug_likeness(check_stability=True)`.

Narrow is the whole point, and the exclusions carry the weight:

- **Ring membership.** `!R` on the central carbon spares benzodioxole
  (paroxetine); acyclic-only peroxide spares artemisinin's endoperoxide.
- **Acylation.** Excluding N/O bound to a carbonyl spares hydroxamic acids
  (vorinostat), esters, and amides.

Validated both ways: catches exactly the two motifs the reference campaign
produced, and flags **0 of 250** approved drugs.

RDKit's BRENK catalog was evaluated for the in-loop gate and rejected — it flags
aspirin (phenol ester) and every aniline, both common in approved drugs. BRENK
and PAINS still run in triage, where a flag is advisory rather than a hard reject.

Triage also now reports `unstable_motif:*` flags, so findings from campaigns that
predate this gate are labelled rather than silently trusted.

### D2 — molecule bounds are per-target (`fuzzer/config.py`, `targets/*/config.yaml`)

`molecules` now merges per-target like `oracle` and `docking`, because viable
chemical space is a property of the target rather than a global constant:

| Target | Override | Why |
|---|---|---|
| hiv_protease | `max_mw: 750`, `max_rot_bonds: 16`, `max_hbd: 12`, `max_hba: 14` | peptidomimetics: indinavir 614 Da, saquinavir 671, ritonavir 721 |
| braf_v600e | `max_logp: 6.0` | vemurafenib is logP 5.54; type-II kinase inhibitors are lipophilic |

Without these, two of five targets silently dropped their own reference drug at
the preparation step — and with it the entire chemical class that works on them.

Prep failures are no longer silent: `Campaign._calibrate` logs a `[PREP]` warning
naming the molecule and the bound most likely responsible. A corpus entry that
cannot be prepared is a known drug the campaign will never test, which is worth a
line in the log.

`test_every_target_can_prepare_its_own_reference_drug` now asserts this for every
bundled target, so adding a target whose bounds exclude its own drug fails the
suite. It caught braf_v600e immediately after hiv_protease was fixed.

**Cost note.** Admitting indinavir-class chemistry makes hiv_protease genuinely
slower: a 45-heavy-atom, 11-rotatable-bond ligand takes ~1–3 min per dock on CPU
at exhaustiveness 8, so one iteration (~30 mutants over 4 workers) runs ~15 min.
That is the real price of searching the space where this target's drugs actually
live — the previous speed came from silently declining to search it. On a GPU
this largely disappears; on CPU, lower `mutations_per_entry` for this target.

---

## Operations

### E1 — checkpointing and resume

- **Time-based checkpointing.** `checkpoint_every_seconds` (default 300),
  whichever comes first with the iteration count. Iteration count alone is a bad
  clock: one iteration is a whole batch of docks.
- **`--resume RUN_DIR`.** Restores corpus, coverage, and the findings index, and
  keeps writing to the same run directory (`storage/open_run_dir`, which
  reattaches rather than allocating a suffixed sibling the way `make_run_dir`
  does by design). Version-mismatched coverage checkpoints warn and start fresh
  rather than killing the resume.
- **`--seed`** for reproducible stage selection.

Verified live: a run checkpointed, was resumed, restored 279 entries and
coverage 0.604, and continued.

### E2 — throughput

- **Dedup.** Canonical SMILES already docked are skipped; `docks_skipped` is
  tracked and reported. Docking is ~100% of the loop's cost.
- **Persistent pool.** One `ProcessPoolExecutor` per campaign instead of one per
  iteration.
- **Parallel prep.** Conformer embedding and MMFF optimisation run on the same
  pool via `prepare_worker` instead of serially on the main process.

### E3 — findings dedup

`FindingsStore` keeps a canonical-SMILES index and suppresses repeats, returning
`None`. It re-indexes existing findings on construction, so a resumed run doesn't
re-save what's already on disk. `dedupe=False` overrides.

---

## Triage

### F1 — selectivity is now ΔΔG

`selectivity_ratio` → `selectivity_ddg` (kcal/mol, positive = prefers on-target),
plus `selectivity_fold` (`10^(ΔΔG/1.364)`) — the Kd fold-difference a chemist
actually quotes — and `offtarget_affinity`. Default floor **1.4 kcal/mol** (one
log unit, ~10× selective).

Triage now also records per-function detail (`vina_affinity`, `cnn_affinity_kcal`,
`cnn_pose_score`, `score_disagreement`) and honours the configured scoring policy,
so confirmation docking and the fuzzing loop no longer disagree about what a
score means.

Confirmation docking also sources strain from `intramol` (A2), so
`strain: null` is gone from triage output too.

---

## Configuration

### G1 — per-target overrides merge (`fuzzer/config.py`)

`merge_defaults` now merges `docking` and `triage` section-by-section instead of
overwriting `docking` from the global config.

### G2 — thresholds are measured (`scripts/calibrate_oracle.py`)

New script. Docks each target's approved reference drug under the *fuzzing*
settings and prints every number the oracle gates on, flagging any reference drug
the current config would reject.

Per-target `affinity_threshold` is now **measured reference score + 1 kcal/mol** —
the same intent as the original calibration table, but anchored to what this
pipeline measures rather than to a published number:

| Target | Old | New | Why |
|---|---|---|---|
| braf_v600e | -9.5 | **-10.3** | reference measures -11.33; old was too loose |
| egfr_kinase | -7.0 | **-6.3** | reference measures -7.30 |
| hiv_protease | -10.0 | **-11.1** | reference measures -12.12; old was too loose |
| parp1 | -11.0 | **-9.5** | **reference only reaches -10.45 — old threshold was unreachable** |
| sars_cov2_mpro | -8.0 | **-7.7** | reference measures -8.66 |

Also: `exhaustiveness_fuzz` 4 → 8 (partially addresses the ~1–2 kcal/mol
initial-vs-confirmed noise in A4), and triage docking timeouts 300s → 900s, since
the full CNN ensemble takes ~97s per dock on an idle CPU here and confirmation
runs at exhaustiveness 16.

Thresholds are reproducible at any time:

```sh
python scripts/calibrate_oracle.py            # all targets
python scripts/calibrate_oracle.py --target parp1
```

---

## Verification

### The same smoke run, before and after

A one-iteration run against hiv_protease (`--max-iterations 1 --seed 3`) is the
whole change set in miniature. Before the D2 fix:

```
docks=0  hits=0  best_affinity=None
```

The top-priority seed was hiv_protease's target-specific one, it failed
preparation on molecular weight, and the iteration did nothing at all. After:

```
docks=31  hits=2  best_affinity=-11.18   union=0.583  corpus=281  scaffolds=236
```

Both hits pass **every** tier — and they are real medicinal chemistry on a
validated HIV protease scaffold, from a seed the tool previously could not even
prepare:

| | Hit 1 | Hit 2 |
|---|---|---|
| mutation | `substituent_scan:NH2` | `halogen_scan:add_Cl` |
| consensus affinity | **-11.18** | **-11.17** |
| vina / CNN | -11.32 / -11.18 | -11.17 / -11.24 |
| CNN pose score | **0.864** | **0.810** |
| strain | -0.95 | -0.21 |
| ligand efficiency | 0.266 | 0.266 |
| unstable motifs | **none** | **none** |
| tiers passed | affinity, strain, LE, CNN pose | affinity, strain, LE, CNN pose |

The CNN pose scores (0.864, 0.810) sit squarely in the **reference-drug** band
(0.801–0.980), not the old findings' band (0.108–0.314). vina and the CNN agree
to within 0.15 kcal/mol, so consensus scoring is affirming rather than merely
tolerating them. And the threshold they cleared (-11.1) is within 1 kcal/mol of
indinavir's measured -12.12.

Compare the reference campaign: **2 hours → 10 chemically impossible molecules
from one scaffold**. Now: **1 iteration (~24 min) → 2 plausible peptidomimetic
leads under a materially stricter oracle**.

### Everything else

- **5/5 reference drugs pass** the corrected oracle.
- **0/10 reference-campaign findings survive** it (9 unstable-motif, 1 duplicate) —
  and 9 are rejected *before docking*, so the compute is never spent.
- **0/250 approved seeds** falsely flagged by the chemistry filter.
- **5/5 targets can prepare their own reference drug** (was 3/5).
- End-to-end fuzz run, resume, and triage all exercised against the real target.
- 149 tests passing.

New test files: `test_scoring.py`, `test_alerts.py`, `test_scheduler.py`,
`test_calibration.py`, `test_resume.py`. Each of the findings above that is
mechanically checkable has a test named after the defect, so a regression
reintroduces a failure rather than quietly returning to the old behaviour.
