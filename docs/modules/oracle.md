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
- Return a structured verdict with which checks passed

**Does not:**
- Re-dock at higher exhaustiveness (Triage)
- Check selectivity against off-targets (Triage)
- Predict ADMET properties (Triage)
- Cluster binding modes or assess pose geometry (Triage)
- Filter for PAINS or reactive groups (Triage; the *mutator* rejects
  chemically implausible molecules before they are ever docked)
- Know anything about coverage or the corpus

Ligand efficiency was originally listed here as triage-only. It was moved into
the loop because it is derivable from the single docking result the oracle
already has — it costs one heavy-atom count — and leaving it to triage meant the
campaign spent its entire budget climbing the molecular-weight gradient before
anything noticed. See `docs/evaluation_2026-07.md` §A3.

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
drug for, so nothing in the gate may be anchored to a drug you already have.
Targets used to ship their own `affinity_threshold` measured as
`reference_score + 1.0 kcal/mol`; that reliance has been removed.

An absolute affinity cutoff is inherently coarse without a per-target anchor — it
will be loose on tight-binding targets and strict on weak ones — which is exactly
why the reference-free quality tiers below (ligand efficiency, CNN pose
confidence, vina/CNN consensus) do the real discriminating. If you want a
per-target notion of "good", rank hits by score in triage rather than tuning this
gate back to a specific molecule.

### Tier 2: Strain Energy
```
intramol ≤ strain_threshold   (default 3.5 kcal/mol)
```
**gnina does not report strain in pose REMARKs.** It reports it as the
`intramol` column of the log's mode table (`DockingMode.intramol`). An earlier
version of this document claimed otherwise, the implementation followed the
document, and the result was that every finding recorded `strain: null` and this
tier never gated anything. A pose-REMARK regex is retained only as a fallback for
engines that do annotate poses.

If no intramolecular term is available the tier is skipped — not failed.

Measured reference-drug strain: -1.18 to +0.60 kcal/mol.

### Tier 3: Ligand Efficiency
```
LE = -score / heavy_atom_count ≥ min_ligand_efficiency
```
Docking scores grow roughly linearly with heavy-atom count, so tier 1 alone
selects for large, greasy molecules. Skipped (not failed) when no structure is
available to count atoms.

**Calibrate this against real drugs before tightening it.** The conventional 0.3
floor rejects four of the five bundled reference drugs — peptidomimetics like
indinavir (0.269) and nirmatrelvir (0.247) are legitimately LE-poor. Default 0.22.

### Tier 4: Pose Confidence
```
cnn_pose_score ≥ min_cnn_pose_score   (default 0.4)
```
gnina's CNN confidence that the pose is a real binding mode. The sharpest tier
available: reference drugs score 0.801–0.980; all ten findings from one
uncorrected campaign scored 0.108–0.314. Skipped when the engine reports no
pose score.

### Tier 5: Score Agreement (off by default)
```
|vina - cnn| ≤ max_score_disagreement
```

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
```

---

## Interface

```
evaluate(
    modes: list[DockingMode],
    pose_pdbqt: str,
    oracle_config: OracleConfig,
    smiles: str | None = None,            # enables the ligand-efficiency tier
    heavy_atom_count: int | None = None,  # ... or pass the count directly
) -> OracleVerdict
```

This is a pure function. The same inputs always produce the same verdict. It has
no side effects and no external dependencies beyond its inputs.

`smiles` is optional so callers without a structure keep working; without it (or
`heavy_atom_count`) the LE tier is skipped rather than failed.

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
to it. The snapshot below (measured through this pipeline under the fuzzing
settings) is what those five approved drugs score; it illustrates why the fixed
LE floor is 0.22 rather than the textbook 0.3, which would reject the four
LE-poorest of them:

| Target | Reference | vina | CNN | consensus | LE | CNN pose |
|---|---|---|---|---|---|---|
| braf_v600e | Vemurafenib | −11.33 | −12.51 | **−11.33** | 0.343 | 0.968 |
| egfr_kinase | Erlotinib | −7.30 | −9.56 | **−7.30** | 0.252 | 0.801 |
| hiv_protease | Indinavir | −12.12 | −13.15 | **−12.12** | 0.269 | 0.938 |
| parp1 | Talazoparib | −11.90 | −10.45 | **−10.45** | 0.373 | 0.931 |
| sars_cov2_mpro | Nirmatrelvir | −8.66 | −11.24 | **−8.64** | 0.247 | 0.966 |

These are docking scores, in a different unit from any published Kd — a reminder
that even for validation you must dock the molecule through this pipeline, never
compare against a literature affinity.

---

## Build Criterion

```python
from biofuzz.oracle import evaluate, OracleConfig

cfg = OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5)

# Strong binder with low strain → hit
hit_modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
hit_pose = "REMARK VINA RESULT: -10.5 ...\\nREMARK GNINA INTRA ...\\n"
verdict = evaluate(hit_modes, hit_pose, cfg)
assert verdict.is_hit
assert "affinity" in verdict.passed_tiers

# Weak binder → no hit
weak_modes = [DockingMode(mode=1, affinity=-6.0, rmsd_lb=0.0, rmsd_ub=0.0)]
assert not evaluate(weak_modes, hit_pose, cfg).is_hit

# High strain → no hit
high_strain_pose = "REMARK strain 8.2\\n"
assert not evaluate(hit_modes, high_strain_pose, cfg).is_hit

# Missing strain annotation → still a hit (strain tier skipped, not failed)
no_strain_pose = "REMARK VINA RESULT: -10.5\\n"
verdict_no_strain = evaluate(hit_modes, no_strain_pose, cfg)
assert verdict_no_strain.is_hit
assert verdict_no_strain.strain is None
```
