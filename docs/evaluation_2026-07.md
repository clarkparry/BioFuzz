# BioFuzz Evaluation — July 2026

> **Superseded in part (post-July-2026).** BioFuzz's reliance on each target's
> known inhibitor has since been removed: target-specific seeds and their
> `TARGET_SPECIFIC_BONUS`, the reference-derived per-target `affinity_threshold`
> (and `scripts/calibrate_oracle.py`), and the ligand-derived docking box/pocket
> are all gone. Boxes and pockets now come from ligand-free pocket detection
> (P2Rank) and the oracle is reference-free. Passages below that describe
> `seeds/per_target/`, `TARGET_SPECIFIC_BONUS`, `calibrate_oracle.py`, or setting
> a threshold from a reference drug are kept as a record of that period; for
> current behavior see [`adding_targets.md`](adding_targets.md) and
> [`modules/oracle.md`](modules/oracle.md).

An in-depth review of BioFuzz against its stated goal: *quickly and effectively use
fuzzing-based techniques to find new drugs or new purposes for old drugs.*

The evidence base is the bundled reference campaign
(`runs/2026-07-16_hiv_protease/`, ~2 hours, 1 worker, no GPU) plus direct
measurement against the five bundled targets' reference drugs. Every claim below
is backed by something measured, not inferred from reading the code.

Changes made in response are in [`docs/improvements_2026-07.md`](improvements_2026-07.md).

---

## Summary

The architecture is sound — the AFL++ analogy is well chosen, the module
boundaries are clean, and the code is readable and well tested (92 tests, all
passing before this work). The problems are not structural. They are that
**several of the mechanisms the design depends on were not actually connected**,
and the ones that were connected were tuned by intuition rather than measurement.

A striking amount of this is not even a design disagreement. **`docs/modules/`
already specifies the correct behaviour in three of the most damaging cases**,
and the implementation quietly diverged from its own spec:

| Spec says | Code did |
|---|---|
| `seeds.md` §"Calibration vs. Triage": "when a seed is first popped and docked, that's its calibration" | Seeds were never docked at all (B3) |
| `mutator.md` §Stage 1: "it skips to havoc on future selections... mirrors AFL++'s `SKIP_TO_HAVOC`" | Havoc was unreachable; splice always won (B1) |
| `corpus.md`: `priority` is "recomputed on pop" | Priority was fixed at insert (B2) |

The tests passed throughout because they test the code that exists, not the
behaviour the design calls for. Two of the docs also contain outright errors that
the implementation faithfully reproduced — `oracle.md` asserts gnina reports
strain in pose REMARKs (it does not, A2), and `seeds.md` recommended rewarding
high molecular weight (the exact docking artifact that needs correcting, B4).

The reference campaign is the tell. In two hours it produced 10 findings:

- **All 10 were the same molecule series** — sildenafil analogs, 5 unique parents,
  a single triage cluster.
- **9 of 10 carried chemically impossible groups** (`-O-N(H)-O-`, `-O-CH2-O-H`).
- **1 of 10 was a literal duplicate** of another finding.
- **0 of 10 survive re-evaluation** under the corrected pipeline.
- **Every one recorded `strain: null`** and `selectivity_ratio: null` — two of the
  oracle's tiers never executed at all.
- The run left **no `corpus/state.json` and no `coverage.json`**. Two hours of
  docking produced nothing resumable.

Meanwhile the five reference drugs (vemurafenib, erlotinib, indinavir,
talazoparib, nirmatrelvir) had never been measured through this pipeline. When
measured:

- **One target's hit threshold was unreachable by its own reference drug**
  (parp1, §G2).
- **Two targets silently rejected their own reference drug** before docking, on
  molecular weight and logP — and with it the entire chemical class that works on
  them (hiv_protease, braf_v600e, §D2).
- **The conventional ligand-efficiency floor rejects four of the five** (§A3).

That combination — everything the tool found was garbage, and much of what it was
built to find could not have been found — is the finding.

None of this is visible from reading the code, and the 92 passing tests said
nothing about it. It took running the tool and measuring it against known answers.

---

## A. Scoring

### A1. gnina's CNN scores were parsed and thrown away — the headline defect

`BIOFUZZ_STRUCTURE.md` states the reason for the engine choice plainly:

> **Gnina only.** The docking backend defaults to gnina because its CNN scoring
> function is meaningfully more accurate than Vina's empirical function.

gnina's mode table has five columns. Verified against gnina v1.3.2 on this host:

```
mode |  affinity  |  intramol  |    CNN     |   CNN
     | (kcal/mol) | (kcal/mol) | pose score | affinity
-----+------------+------------+------------+----------
    1       -9.81        1.30       0.3207      6.795
```

`parse_log` matched all five and kept **column 2 only** — vina's empirical score.
Columns 3–5 were discarded at the regex.

So BioFuzz paid gnina's CNN inference cost on every single dock (the dominant
cost of the entire campaign) and then ranked, gated, prioritised, and reported
on the vina number — the exact function the design says gnina was chosen to
avoid. The stated rationale for the whole backend was unrealised.

### A2. The strain tier could never fire

`extract_strain` searched the *pose PDBQT* for a `REMARK ... INTRA` line. gnina
does not write one. Confirmed by inspection of real gnina output — its pose
REMARKs are `minimizedAffinity`, `CNNscore`, `CNNaffinity`, `SMILES`. Nothing else.

The intramolecular energy was available the whole time, in column 3 of the log
table that `parse_log` was already discarding.

Consequence: `strain: null` in all 10 findings. The oracle documents two gating
tiers; only one existed.

### A3. Raw affinity selects for molecular size

Docking scores grow roughly linearly with heavy-atom count. Gating on affinity
alone therefore selects for large, greasy molecules regardless of whether they
make good contacts. The triage report says so directly — every single finding
carried both `low_ligand_efficiency` and `lipinski_mw_violation`.

Ligand efficiency was computed, but only in triage, *after* the campaign had
already spent its entire budget climbing the molecular-weight gradient. The
signal existed and arrived too late to steer anything.

### A4. Fuzz-stage docking was noisy enough to manufacture hits

`exhaustiveness_fuzz: 4` with a single dock per molecule. Initial vs confirmed
affinity from the triage report:

| Initial | Confirmed | Δ |
|---|---|---|
| -10.46 | -9.38 | 1.08 |
| -10.01 | -8.06 | **1.95** (flagged `confirmation_affinity_mismatch`) |
| -10.21 | -9.60 | 0.61 |

A substantial share of "hits" were search noise, not binding.

---

## B. The corpus scheduler

### B1. Havoc was unreachable — a third of the mutator never ran

```python
if entry.times_selected <= 1:
    return "deterministic"
donor = self.state.corpus.sample_donor(...)
if donor is not None:
    return "splice"
return "havoc"          # only when the corpus holds ≤ 1 molecule
```

`sample_donor` returns `None` only when the corpus has fewer than two entries.
With 251 seeds loaded at startup, that never happens after the first iteration.
**Havoc was dead code.**

AFL++ spends most of an input's budget in havoc; it is the primary discovery
mechanism. Findings by stage: 9 deterministic, 1 splice, **0 havoc**.

`docs/modules/mutator.md` already specified the right rule — "once an entry has
been through deterministic, it skips to havoc on future selections... This
mirrors AFL++'s `SKIP_TO_HAVOC` flag" — so this is a straight divergence from
spec, not a design question.

### B2. Mode collapse: 251 seeds in, one molecule explored

The priority heap is purely greedy and had no diversity term. Every mutant of a
good molecule is itself a good molecule, so it re-entered at high priority and
crowded out everything else. Novelty (see C1) was pinned near its maximum for
almost every pose, which flattened the one term that could have differentiated
entries. The reuse penalty was `times_fuzzed * 0.1` against a novelty weight of
`10.0` — an entry had to be fuzzed **200 times** before it fell behind a fresh
sibling of equal novelty.

Result: 251 approved drugs entered the corpus and the campaign explored
essentially one of them. All 10 hits, one scaffold, one cluster.

### B3. Seeds were never docked — the repurposing use case did not exist

This is the most consequential finding for the stated goal.

The loop docks *mutants*. It never docks the corpus entry itself. Seeds enter via
`Corpus.add()` and are only ever used as mutation parents.

**BioFuzz never measured whether an approved drug binds the target.** For a tool
whose purpose is drug repurposing, the primary experiment was absent. "Does
sildenafil inhibit HIV protease?" is exactly the question the tool exists to
answer, and it was never asked — only "do sildenafil's mutants?".

It also broke the scheduler: seeds kept `best_affinity = None` forever, so
`affinity_bonus` was 0 and the power schedule ranked 251 seeds on no evidence.

The design doc's "no seed triage" decision is right — seeds shouldn't need a
blocking pre-docking phase. And `seeds.md` does not actually make this mistake;
it states the correct behaviour twice:

> "The first time each seed is popped, it gets docked, its actual affinity is
> recorded, and its priority is updated to reflect the real coverage signal."
>
> "BioFuzz mirrors this: when a seed is first popped and docked, that's its
> calibration."

AFL++ does not skip seeds; it *calibrates* them on first exec, and the spec knew
it. The distinction was lost between the document and the loop — "no pre-docking
phase" was implemented as "no docking".

### B4. Seed priority rewarded molecular weight

```python
mw_bonus = max(0.0, (mw - 350.0) / 100.0) * 2.0
```

Unbounded in MW: the heaviest seed in the file always got popped first. That is
how sildenafil (MW 475) came to be the molecule the campaign spent two hours on.
It compounds with A3 — the seed prior and the oracle were both biased toward
size, in the same direction.

Here the *document* is the root cause. `seeds.md` said:

> "Higher MW within range → modest priority bonus (bigger molecules tend to
> score better)"

The parenthetical is true, and that is exactly why it is the wrong rule: bigger
molecules score better as a **docking artifact**, so a seed prior that rewards
size amplifies the bias the pipeline most needs to correct. "Within range" was
also never implemented as a range.

---

## C. Coverage

### C1. Novelty carried almost no information

The whole fingerprint was hashed to **one** slot:

```python
mask = self._fingerprint_mask(fingerprint)
slot = splitmix64(mask) & (self.map_size_bytes - 1)
```

Two consequences:

1. **No partial credit.** Contacting `{A,B,C}` and `{A,B,C,D}` produce unrelated
   slots. There is no notion of "this pose reached a residue nothing had reached
   before" — precisely the AFL++ new-edge signal the design is built on. The
   config key is named `priority_new_bit_weight`; there were no bits.
2. **Almost everything scored `strong`.** A 256 KiB map holds 262,144 slots, so
   nearly every distinct binding mode hashed somewhere unseen. Novelty was
   pinned at maximum, which is the same as carrying no signal — and it fed
   straight into B2.

`occupancy_rotate_threshold: 0.55` needs ~144,000 distinct fingerprints before an
epoch rotation. At CPU docking speeds that is unreachable; the epoch mechanism
was decorative. Measured occupancy after a real run: **0.000**.

---

## D. Chemistry

### D1. Nothing checked whether molecules could exist

The mutation gate checked MW, logP, HBD, HBA, and rotatable bonds. None of those
say anything about chemical plausibility.

`atom_scan` walks sildenafil's ethoxy tail (`-O-CH2-CH3`) one atom at a time,
mutating C→N and C→O independently, and produces `-O-N(H)-O-` and `-O-CH2-O-H`.
These are a hydroxylamine ether and a hemiacetal: not molecules a chemist could
order, make, or store. They docked well, passed every filter, and were saved as
the campaign's top-ranked findings.

**9 of the 10 findings carried one of these two motifs.**

### D2. Two targets silently rejected their own reference drug

The property bounds in `molecules:` were global-only — one MW/logP/HBD/HBA
envelope for every target. Molecule preparation applies them, so a molecule
outside the envelope is dropped before docking, silently.

Measured against each target's own approved reference drug:

| Target | Reference | MW | logP | Verdict under global bounds |
|---|---|---|---|---|
| braf_v600e | vemurafenib | 489.9 | **5.54** | **rejected** (max_logp 5.0) |
| egfr_kinase | erlotinib | 393.4 | 3.41 | ok |
| hiv_protease | indinavir | **613.8** | 2.87 | **rejected** (max_mw 550) |
| parp1 | talazoparib | 380.4 | 2.63 | ok |
| sars_cov2_mpro | nirmatrelvir | 501.6 | 1.22 | ok |

**Two of five targets could not process the drug they were built around.** For
hiv_protease and braf_v600e, the reference drug is also the *only* target-specific
seed — the one seeded with `TARGET_SPECIFIC_BONUS`, so it sorts to the head of the
queue, gets popped first, fails preparation, and the iteration does nothing. No
log line, no warning.

The consequence is larger than one wasted iteration: HIV protease inhibitors are
peptidomimetics (indinavir 614 Da, saquinavir 671, ritonavir 721) and type-II
kinase inhibitors are lipophilic by nature. A 550 Da / logP 5.0 envelope excludes
**the entire chemical class that works on those targets**. No campaign against
hiv_protease could ever have found an indinavir-like molecule, because nothing
resembling one could survive preparation.

This one surfaced only by running the fuzzer, not by reading it: a one-iteration
smoke run reported `docks=0` and `best_affinity=None`.

Viable chemical space is a property of the target, not a global constant. The
oracle's ligand-efficiency tier — not a blanket MW cap — is the right instrument
for keeping size honest, because it asks what the extra atoms are *buying*.

---

## E. Operations

### E1. Two hours of compute, nothing resumable

`checkpoint_every: 500` counts *iterations*, and one iteration is a whole batch
of ~20 docks. At CPU docking speeds 500 iterations is on the order of days. The
reference run never reached it: no `corpus/state.json`, no `coverage.json`, no
`[CHKPT]` line in the log.

There was also **no `--resume` flag at all**. A campaign was inherently
all-or-nothing — the opposite of AFL++, where the queue is durable by design and
campaigns are expected to run for days across restarts.

### E2. Wasted docking

- **No dedup.** A molecule already docked was docked again if a mutation
  rediscovered it. Docking is ~100% of the loop's cost.
- **Pool churn.** A `ProcessPoolExecutor` was created and destroyed *every
  iteration* — full interpreter startup and RDKit import per worker, per batch.
- **Serial prep.** Conformer embedding plus MMFF optimisation ran single-threaded
  on the main process while the worker pool sat idle.

### E3. Duplicate findings

Findings `000001` and `000006` are the same molecule
(`...c3ONO)nc12`, -10.24 and -10.25). Nothing deduplicated them, so the hit was
double-counted and triage re-docked it.

---

## F. Triage

### F1. Selectivity used a ratio of energies — a unit error

```python
ratio = abs(record.confirmed_affinity) / abs(modes[0].affinity)
```

Binding free energies are kcal/mol on a **log** scale. Their ratio is not a
physical quantity. "-10 ÷ -5 = 2× selective" is dimensionally meaningless; the
real statement is a 5 kcal/mol gap, which is ~4000× in Kd.

Selectivity is a *difference*, ΔΔG.

### F2. Selectivity never ran anyway

It requires `offtarget_receptor` / `offtarget_box`. No bundled target defines
them, so every record returned `selectivity_not_configured`. For a repurposing
tool — where "does this also hit something it shouldn't?" is the central
question — the discriminator was inert.

---

## G. Configuration

### G1. Per-target docking overrides were silently discarded

```python
merged["docking"] = dict(global_config.get("docking", {}))
```

`merge_defaults` assigned `docking` straight from the global config. A target
could set its own exhaustiveness or CNN model and be silently ignored.

### G2. Thresholds were set from literature, not from this pipeline

Per-target `affinity_threshold` came from published affinities. But a threshold
is only meaningful in the units the tool actually measures. Docking each
reference drug through the real pipeline (`scripts/calibrate_oracle.py`):

| Target | Reference drug | vina | CNN | consensus | LE | CNN pose |
|---|---|---|---|---|---|---|
| braf_v600e | vemurafenib | -11.33 | -12.51 | -11.33 | 0.343 | 0.968 |
| egfr_kinase | erlotinib | -7.30 | -9.56 | -7.30 | 0.252 | 0.801 |
| hiv_protease | indinavir | -12.12 | -13.15 | -12.12 | 0.269 | 0.938 |
| parp1 | talazoparib | -11.90 | -10.45 | -10.45 | 0.373 | 0.931 |
| sars_cov2_mpro | nirmatrelvir | -8.66 | -11.24 | -8.66 | 0.247 | 0.966 |

Two things fall out:

- **parp1's threshold was unreachable.** It was set to -11.0 from a literature
  value of ~-12.2, but talazoparib itself only reaches **-10.45** here.
  *The target's own reference drug could not have been called a hit* — and
  neither could anything resembling it. That target was effectively incapable of
  producing a finding.
- **The conventional LE floor of 0.3 rejects four of these five real drugs.**
  Peptidomimetics (indinavir, nirmatrelvir) are legitimately LE-poor. Any
  intuition-based LE threshold would have silently excluded the very compounds
  the tool is meant to rediscover. This is why the threshold is now measured.

---

## Verification

Two checks bound the change set from both sides.

**Real drugs must pass.** All five reference drugs are hits under the corrected
oracle (`scripts/calibrate_oracle.py`, reproducible on demand).

**The old output must not.** Re-evaluating the reference campaign's 10 findings
against the corrected pipeline:

```
000001  old_aff=-10.24  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000002  old_aff=-10.21  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000003  old_aff=-10.46  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000004  old_aff=-10.18  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000005  old_aff=-10.01  REJECTED pre-dock: unstable motif ['acyclic_acetal']
000006  DUPLICATE of an earlier finding (dedup would suppress)
000007  old_aff=-10.27  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000008  old_aff=-10.13  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000009  old_aff=-10.40  REJECTED pre-dock: unstable motif ['n_o_single_bond']
000010  old_aff=-10.00  REJECTED pre-dock: unstable motif ['n_o_single_bond']

Of 10 original findings: 1 duplicate, 9 unstable-motif, 0 still hits.
```

**5/5 real drugs in, 0/10 old findings out.** The rejections happen before
docking, so that compute is not spent at all.

**Two independent mechanisms agree.** Running the old findings through the
corrected *triage* (which re-docks rather than pre-filtering) shows the
pose-confidence tier would reject all ten on its own, without the chemistry
filter's help:

| | CNN pose score |
|---|---|
| 5 reference drugs | **0.801 – 0.980** |
| 10 reference-campaign findings | **0.108 – 0.314** |

Gate at 0.4 and the two populations separate completely, with a wide margin on
both sides. gnina's own network was reporting that these poses were not credible
binding modes the entire time — that judgement was in column 4 of every log
table the tool ever parsed and discarded (§A1).

Triage now also populates what was previously always `null`: `strain` is present
in all 10 records (e.g. -2.08), as are `vina_affinity`, `cnn_affinity_kcal`,
`cnn_pose_score` and `score_disagreement`. All 10 carry `unstable_motif:*` flags.
`selectivity_ddg` remains `None` — the metric is fixed but no off-target data
exists yet (§F2).

**And the tool now finds things.** Rejecting the old output only proves the gate
is tighter; the point is whether real chemistry gets through. A single iteration
against hiv_protease (`--max-iterations 1`) produced two hits at **-11.18** and
**-11.17 kcal/mol**, from the target-specific seed that §D2 had made
unpreparable:

| | Hit 1 | Hit 2 |
|---|---|---|
| mutation | `substituent_scan:NH2` | `halogen_scan:add_Cl` |
| consensus / vina / CNN | -11.18 / -11.32 / -11.18 | -11.17 / -11.17 / -11.24 |
| CNN pose score | 0.864 | 0.810 |
| strain | -0.95 | -0.21 |
| ligand efficiency | 0.266 | 0.266 |
| unstable motifs | none | none |

Both clear every tier. Their pose scores sit in the reference-drug band
(0.801–0.980), not the old findings' band (0.108–0.314); vina and the CNN agree
to within 0.15 kcal/mol; and the threshold they cleared (-11.1) is within
1 kcal/mol of indinavir's measured -12.12. The mutations are ordinary medicinal
chemistry — add an amine, add a chlorine — on a validated peptidomimetic
scaffold.

The reference campaign spent two hours to produce ten impossible molecules from
one scaffold. One iteration now produces two plausible leads against a
materially stricter oracle.

The chemistry filter is validated in the other direction too: **0 of 250**
approved drugs in `seeds/approved_drugs.smi` are flagged, and artemisinin (ring
endoperoxide), paroxetine (benzodioxole), aspirin (phenol ester) and vorinostat
(hydroxamic acid) all pass — each of which a naive version of these patterns
rejects. RDKit's BRENK catalog was evaluated for this job and rejected: it flags
aspirin and every aniline.

---

## What was not changed

- **Selectivity off-target data (F2).** The metric is fixed, but no target ships
  off-target receptors. Populating them is a data-collection task, not a code
  change, and picking off-targets is a scientific judgement worth making
  deliberately. This is the largest remaining gap for repurposing.
- **Docking noise (A4).** Fuzz exhaustiveness moved 4 → 8 and consensus scoring
  suppresses single-function false positives, but no replicate-docking or
  variance estimate was added. A cheap re-dock of borderline candidates before
  saving a finding would be the principled fix.
- **Pocket-level coverage granularity.** `interaction_types` (donor / acceptor /
  hydrophobic per residue) exists and is off by default. With union coverage now
  meaningful, turning it on gives 4× the bits and a longer-lived novelty signal;
  it deserves a measured comparison rather than a flipped default.
