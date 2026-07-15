# Module: Oracle

**AFL++ analog:** The crash detector — did this input trigger the bug condition?

The oracle answers one question during fuzzing: "Is this molecule a hit worth saving?" It must be fast, simple, and derivable from a single docking result — no re-docking, no external calls, no human review. Anything that requires additional computation belongs in the Triage module.

The AFL++ equivalent is the crash signal: AFL doesn't try to analyze whether a crash is exploitable in the fuzzing loop — it just saves it and moves on. BioFuzz does the same: save anything that clears the affinity and strain bars, and triage it later.

---

## Boundary

**Does:**
- Check affinity against a configurable threshold
- Check internal strain energy if the docking engine reports it
- Return a structured verdict with which checks passed

**Does not:**
- Re-dock at higher exhaustiveness (Triage)
- Check selectivity against off-targets (Triage)
- Predict ADMET properties (Triage)
- Assess pose quality or ligand efficiency (Triage)
- Filter for PAINS or reactive groups (Triage)
- Know anything about coverage or the corpus

---

## Hit Conditions

A molecule is a hit if and only if it passes **all enabled tiers**.

### Tier 1: Affinity Threshold (always enabled)
```
modes[0].affinity ≤ affinity_threshold
```
`affinity_threshold` is set per target in `targets/<name>/config.yaml`. It should be calibrated to approximately `reference_inhibitor_affinity - 1.0 kcal/mol`. Using a global threshold across all targets produces too many false positives on tight-binding targets and misses hits on weaker targets.

### Tier 2: Strain Energy (enabled when engine reports it)
```
strain_energy ≤ strain_threshold   (default 3.5 kcal/mol)
```
Gnina reports internal strain energy in REMARK lines of the pose PDBQT. If the strain annotation is absent (gnina version doesn't emit it, or Vina is used), this tier is skipped — not treated as a failure. The strain regex must be strict enough to match only known gnina REMARK formats, not arbitrary lines containing the word "strain."

---

## OracleVerdict

```
OracleVerdict:
  is_hit: bool
  affinity: float           # best mode affinity (kcal/mol)
  strain: float | None      # internal strain if reported, else None
  passed_tiers: list[str]   # ["affinity", "strain"] etc.
  notes: str                # human-readable reason for non-hit, or "passed"
```

---

## Interface

```
evaluate(
    modes: list[DockingMode],
    pose_pdbqt: str,
    oracle_config: OracleConfig,
) -> OracleVerdict
```

This is a pure function. The same inputs always produce the same verdict. It has no side effects and no external dependencies beyond the two inputs.

`OracleConfig` contains only the thresholds — it does not contain anything about the target receptor or pocket.

```
OracleConfig:
  affinity_threshold: float    # e.g. -9.0 kcal/mol
  strain_threshold: float      # e.g. 3.5 kcal/mol
```

---

## What "Fast" Means Here

The oracle's compute time should be effectively zero relative to a docking call. Both checks are regex/arithmetic operations on text that was already produced by the docking run. If the oracle ever needs to make a subprocess call or network request, that code belongs in Triage.

---

## Threshold Calibration Guidance

Per-target thresholds should be set before the campaign starts, not globally:

| Target | Reference inhibitor | Reference affinity | Suggested threshold |
|---|---|---|---|
| hiv_protease | Indinavir | ~−11 kcal/mol | −10.0 |
| egfr_kinase | Erlotinib | ~−7.2 kcal/mol | −7.0 |
| parp1 | Talazoparib | ~−12.2 kcal/mol | −11.0 |
| sars_cov2_mpro | Nirmatrelvir | ~−8.3 kcal/mol | −8.0 |
| braf_v600e | Vemurafenib | ~−10.1 kcal/mol | −9.5 |

Setting the threshold ~1 kcal/mol below the reference gives a campaign something to aim for without being so tight that only the reference itself passes.

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
