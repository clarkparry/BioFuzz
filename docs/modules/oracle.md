# Module: Oracle

**AFL++ analog:** The crash detector — did this input trigger the bug condition?

The oracle answers one question during fuzzing: "Is this molecule a hit worth saving?" It must be fast, simple, and derivable from a single docking result — no re-docking, no external calls, no human review. Anything that requires additional computation belongs in the Triage module.

The AFL++ equivalent is the crash signal: AFL doesn't try to analyze whether a crash is exploitable in the fuzzing loop — it just saves it and moves on. BioFuzz does the same: save anything that clears the affinity and strain bars, and triage it later.

---

## Boundary

**Does:**
- Reduce a docking result to one score under a configurable scoring policy
- Check that score against the global reference-free affinity threshold
- Check internal strain energy (the engine's intramolecular term)
- Check ligand efficiency, so raw score can't select on molecular size alone
- Check the engine's own pose confidence, where it reports one
- Check that the pose *engages the essential residues* — where it binds, not just
  how tightly — from a precomputed contact count the caller supplies
- Return a structured verdict with which checks passed

**Does not:**
- Re-dock at higher exhaustiveness (Triage)
- Check selectivity against off-targets (Triage)
- Predict ADMET properties (Triage)
- Cluster binding modes (Triage)
- Compute pose geometry itself — the essential-residue *count* is worked out by
  the caller (which owns the receptor) and passed in, exactly like
  `heavy_atom_count`, so `evaluate()` stays a pure function of scalars
- Filter for PAINS or reactive groups (Triage; the *mutator* rejects
  chemically implausible molecules before they are ever docked)
- Know anything about coverage or the corpus

Ligand efficiency sits in the loop rather than in triage, even though it reads
like a quality metric, because it is derivable from the single docking result the
oracle already has: it costs one heavy-atom count. Leaving it to triage would let
a campaign spend its whole budget climbing the molecular-weight gradient before
anything noticed.

---

## Scoring policy

gnina reports two independent estimates per mode: vina's empirical `affinity`
(kcal/mol, negative = tighter) and the CNN's `cnn_affinity` (pKd, positive =
tighter). `biofuzz/oracle/scoring.py` converts the CNN prediction to kcal/mol
(ΔG = -1.364 × pKd, RT·ln10 at 298 K) and combines them:

| policy | score |
|---|---|
| `vina` | vina only |
| `cnn` | CNN only |
| `consensus` *(default)* | the weaker (least negative) of the two |

`consensus` requires both functions to agree a molecule binds. Each function's
false positives are largely the other's rejects.

The best mode is re-selected under the active policy rather than trusting
`modes[0]`: gnina sorts by whichever function it was told to rank with, which
need not be the one being gated on.

---

## Hit Conditions

A molecule is a hit if and only if it passes **all enabled tiers**.

### Tier 1: Affinity Threshold (always enabled)
```
best_mode_score(policy) ≤ affinity_threshold
```
`affinity_threshold` is a **single reference-free value in the global
`config.yaml`**, inherited by every target. It is deliberately *not* derived from
any known inhibitor: BioFuzz is meant to find binders for a protein you have no
drug for, so nothing in the gate may be anchored to a drug you already have. No
target may override it, and `tests/test_fuzzer.py` asserts that none does.

An absolute cutoff is inherently coarse without a per-target anchor — loose on
tight-binding targets, strict on weak ones. Measured through this pipeline, three
of the five bundled reference drugs clear -9.0 and two do not (see the table
below), which is a real cost rather than a rounding error. It is accepted because
the alternative anchors the gate to the answer. The reference-free quality tiers
below (ligand efficiency, CNN pose confidence, vina/CNN consensus) do the real
discriminating; for a per-target notion of "good", rank hits by score in triage
rather than tuning this gate to a specific molecule.

### Tier 2: Strain Energy
```
intramol ≤ strain_threshold   (default 3.5 kcal/mol)
```
**gnina does not report strain in pose REMARKs.** It reports it as the
`intramol` column of the log's mode table (`DockingMode.intramol`), which is
where `evaluate()` reads it from. A pose-REMARK regex is retained only as a
fallback for engines that annotate the pose instead; gating on that regex alone
would silently disable this tier on gnina.

If no intramolecular term is available the tier is skipped — not failed.

Measured reference-drug strain across three replicates: -1.29 to +0.66 kcal/mol.

### Tier 3: Ligand Efficiency
```
LE = -score / heavy_atom_count ≥ min_ligand_efficiency
```
Docking scores grow roughly linearly with heavy-atom count, so tier 1 alone
selects for large, greasy molecules. Skipped (not failed) when no structure is
available to count atoms.

**Calibrate this against real drugs before tightening it.** Measured through
this pipeline the five bundled reference drugs span LE 0.246 to 0.376, so the
conventional 0.3 floor would reject three of them — erlotinib (0.25), indinavir
(0.27) and nirmatrelvir (0.25), all legitimately LE-poor. Default 0.22.

### Tier 4: Pose Confidence
```
cnn_pose_score ≥ min_cnn_pose_score   (default 0.4)
```
gnina's CNN confidence that the pose is a real binding mode, and the sharpest
tier available. Measured across three replicates the five reference drugs score
0.598–0.981, while poses from unconstrained mutant chemistry commonly land
around 0.1–0.3, so a 0.4 floor separates them with room on both sides. Note that
this number is noisier between runs than the affinity columns: erlotinib alone
moved 0.598 to 0.731. Skipped when the engine reports no pose score.

### Tier 5: Score Agreement (off by default)
```
|vina - cnn| ≤ max_score_disagreement
```

### Tier 6: Essential-Residue Engagement
```
essential_contacts ≥ min_essential_contacts   (default 1)
```
Every tier above is a *scalar*: it says how tightly a pose scores, never **where**
it binds. A molecule can clear all of them while sitting in the wrong sub-pocket,
making none of the contacts that define the site. This tier is the geometric
check that catches that — and it is the answer to "how do we know a hit binds
where it should without knowing anything about the protein?"

The **essential set** is a small group of pocket residues a genuine binder must
touch, derived from the protein alone (never from a known inhibitor). It composes
three structure-only signals, computed in `biofuzz/protein/essential.py`:

1. **Structural (always available).** Score each pocket residue by *burial*
   (deep residues are the anchor points a ligand must reach) and *polar/ionizable
   character* (a charged side chain buried in a pocket is expensive to bury, so
   it is there for a reason). On HIV protease, treated as an unknown protein,
   the top two residues this produces are the catalytic aspartate dyad
   (A:25 / B:25) — recovered without ever naming them.
2. **Ligandability (when a detector ran).** P2Rank's per-residue druggability,
   blended into the ranking at target-prep time.
3. **Emergent (accrues during the campaign).** BioFuzz docks its approved-drug
   seeds to calibrate them; the pocket residues that many *confident* seed poses
   all contact are, empirically, what this pocket demands. This half is
   self-calibrating — the AFL++ calibration analogy applied to residues.

The static half (1 + 2) is written to a target's `pocket.essential_residue_ids`
by the prep tool; if absent (e.g. a hand-curated config), the campaign rederives
the structural set at startup. The emergent half (3) unions in at runtime.

**The gate is deliberately soft.** It is *tolerant* — a pose need only contact
`min_essential_contacts` of the set (default 1, "reach the anchor at all"). And
it *self-disables* per target: if no trustworthy set can be formed (no receptor,
empty pocket), the caller passes no count and the tier is skipped, not failed —
the same skip-don't-fail contract as the ligand-efficiency tier. This guards
against gating on a wrong guess. If you would rather flag than reject, raise the
finding and inspect `essential_contacts` in triage instead of enabling the gate.

Because the essential set encodes a hypothesis about *the* binding mode, it is a
mild bias against novel (e.g. allosteric) modes; keeping the set to genuine
structural anchors and the gate tolerant is what bounds that cost.

---

## OracleVerdict

```
OracleVerdict:
  is_hit: bool
  affinity: float                 # best mode score under the policy (kcal/mol)
  strain: float | None            # intramolecular energy, else None
  passed_tiers: list[str]         # ["affinity", "strain", "ligand_efficiency", ...]
  notes: str                      # human-readable reason for non-hit, or "passed"
  ligand_efficiency: float | None
  heavy_atom_count: int | None
  vina_affinity: float | None     # per-function detail, so a finding records
  cnn_affinity_kcal: float | None # *why* it was called, not just that it was
  cnn_pose_score: float | None
  scoring_policy: str | None
  essential_contacts: int | None  # essential residues this pose engaged, or
                                  # None when the tier did not gate
```

---

## Interface

```
evaluate(
    modes: list[DockingMode],
    pose_pdbqt: str,
    oracle_config: OracleConfig,
    smiles: str | None = None,             # enables the ligand-efficiency tier
    heavy_atom_count: int | None = None,   # ... or pass the count directly
    essential_contacts: int | None = None, # enables the essential-residue tier
) -> OracleVerdict
```

This is a pure function. The same inputs always produce the same verdict. It has
no side effects and no external dependencies beyond its inputs.

`smiles` is optional so callers without a structure keep working; without it (or
`heavy_atom_count`) the LE tier is skipped rather than failed. `essential_contacts`
is likewise optional: the caller (the campaign) owns the receptor, computes how
many essential residues the pose engages, and passes the count; None skips the
tier. This is what keeps geometry out of the oracle while still gating on it.

`OracleConfig` contains only the thresholds — nothing about the target receptor
or pocket.

```
OracleConfig:
  affinity_threshold: float                  # e.g. -9.0 kcal/mol
  strain_threshold: float                    # e.g. 3.5 kcal/mol
  scoring_policy: str = "consensus"          # vina | cnn | consensus
  min_ligand_efficiency: float | None = 0.22 # None disables the tier
  min_cnn_pose_score: float | None = 0.4     # None disables the tier
  max_score_disagreement: float | None = None
  min_essential_contacts: int | None = 1     # None disables the tier
```

---

## What "Fast" Means Here

The oracle's compute time should be effectively zero relative to a docking call. Both checks are regex/arithmetic operations on text that was already produced by the docking run. If the oracle ever needs to make a subprocess call or network request, that code belongs in Triage.

---

## Why the oracle is reference-free

There is no per-target threshold calibration. The hit gate must not be anchored
to a known inhibitor, because the whole premise is finding a binder for a protein
you have no drug for — a gate tuned to the answer both defeats that premise and
inflates apparent performance on the bundled example targets. Every target
inherits the one reference-free oracle in `config.yaml`.

The known inhibitors under `targets/<name>/reference_ligands/` remain as an
optional **validation** set only. Docking them by hand answers "is my oracle so
strict it would reject a real drug?" — a sanity check on the gate, not an input
to it.

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

Read three things off this table.

It sets the LE floor: 0.22 rather than the textbook 0.3, which would reject
erlotinib, indinavir and nirmatrelvir. It sets the pose-confidence floor: all
five clear 0.4 comfortably. And it bounds the affinity tier's honesty — at
−9.0, **hiv_protease, braf_v600e and parp1 pass; sars_cov2_mpro and egfr_kinase
do not**, in all three replicates. Erlotinib in particular is a genuinely modest
docker here. That is the price of refusing a per-target threshold.

Note also that `consensus` tracks vina on four of five targets and the CNN on
parp1, which is the policy working as intended: whichever function is less
convinced sets the score.

These are docking scores, in a different unit from any published Kd — a reminder
that even for validation you must dock the molecule through this pipeline, never
compare against a literature affinity.

---

## Verification

`tests/test_oracle.py` and `tests/test_scoring.py` exercise this module: each
tier passing and failing in isolation, the skip-don't-fail contract for every
optional tier, strain read from the `intramol` column rather than a pose REMARK,
and the three scoring policies against a captured gnina log.

To re-measure the reference-drug table above, dock each
`targets/<name>/reference_ligands/*.smi` through `prepare_smiles` and
`GninaBackend.dock` at the config's `exhaustiveness_fuzz` and `cnn_model_fuzz`,
then pass the parsed modes to `evaluate()`. Run it at least three times per
target: single-run numbers overstate their own precision.
