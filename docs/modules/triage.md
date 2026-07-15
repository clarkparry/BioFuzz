# Module: Triage

**AFL++ analog:** CASR / exploitability analysis — deep post-crash investigation, never in the fuzzing loop

Triage runs after the fuzzing campaign ends, on the contents of `runs/<stamp>/findings/`. It is the CASR analog: slow, thorough, and completely separate from the hot path. The fuzzer saves hits quickly; triage determines which of those hits are scientifically credible.

This is a standalone tool invoked as:
```
biofuzz-triage --findings runs/<stamp>/findings/ --target hiv_protease [options]
```

---

## Boundary

**Does:**
- Re-dock each hit at high exhaustiveness to confirm the score
- Assess pose quality: strain, reproducibility, pose consensus
- Compute ligand efficiency and other binding quality metrics
- Run cheminformatics ADMET filters (no external API, no ML model required)
- Flag chemistry concerns: PAINS, reactive groups, aggregators
- Check target selectivity by docking against a configured off-target
- Cluster binding modes to deduplicate redundant hits
- Produce a ranked, annotated output report

**Does not:**
- Run during the fuzzing campaign (never called from the fuzzer)
- Require human input to complete
- Contact external services (all analysis is local)
- Produce final drug candidates — this is early-stage computational filtering

---

## Triage Pipeline

Each finding passes through all stages. Any stage can produce a flag (a warning that does not discard the molecule) or a filter (which marks it as low-confidence for the final report ranking).

```
findings/
    │
    ▼
[1] Confirmation Docking
    Re-dock at exhaustiveness=16 (or configured value).
    Record best affinity, RMSD spread across top 3 modes.
    Filter: confirmed affinity not within 1.5 kcal/mol of initial → downrank

    ▼
[2] Pose Quality
    Strain energy from gnina REMARK lines
    Pose reproducibility: RMSD spread between modes 1 and 2
    Filter: strain > threshold → downrank
    Flag: RMSD spread > 2.0 Å → pose may not be well-defined

    ▼
[3] Ligand Efficiency
    LE = |affinity| / heavy_atom_count
    Good threshold: LE > 0.3 kcal/mol/heavy atom
    Flag: LE < 0.3 → molecule may be too large for its binding quality

    ▼
[4] ADMET Filters (cheminformatics, no ML)
    Lipinski Rule of Five (MW ≤ 500, HBD ≤ 5, HBA ≤ 10, logP ≤ 5)
    TPSA ≤ 140 Å² (oral bioavailability proxy)
    Rotatable bonds ≤ 10
    Flag: each violation noted in report

    ▼
[5] Chemistry Flags
    PAINS filter (Pan-Assay Interference Compounds — RDKit catalog)
    Reactive group filter (aldehydes, acyl halides, Michael acceptors, etc.)
    Aggregator flag (cationic amphiphiles, certain polyaromatics)
    Flag: any match noted; not an automatic discard — some reactive groups are valid drugs

    ▼
[6] Selectivity Check
    Dock against off-target receptor (configured in targets/<name>/config.yaml)
    selectivity_ratio = |target_affinity| / |offtarget_affinity|
    Flag: selectivity_ratio < threshold (default 2.0)
    Only runs if offtarget_receptor is configured.

    ▼
[7] Binding Mode Clustering
    Cluster all passing hits by pose RMSD (or center-of-mass distance if poses aren't available)
    For each cluster: keep the highest-LE representative
    Report cluster membership so user can see which hits are structurally redundant

    ▼
Ranked Report
```

---

## Report Output

Triage produces a directory `runs/<stamp>/triage/` containing:

```
triage/
├── report.json          # Full structured results, one entry per finding
├── report.html          # Human-readable ranked table (optional, no external deps)
└── top_hits/            # Subdirectory with SMILES + pose for top N candidates
    ├── 001_<smiles_hash>/
    │   ├── pose.pdbqt
    │   ├── metadata.json
    │   └── summary.txt
    └── ...
```

Each entry in `report.json`:
```json
{
  "smiles": "...",
  "initial_affinity": -9.8,
  "confirmed_affinity": -10.1,
  "strain": 1.2,
  "ligand_efficiency": 0.42,
  "selectivity_ratio": 3.1,
  "pose_rmsd_spread": 0.8,
  "heavy_atom_count": 24,
  "molecular_weight": 342.4,
  "logp": 3.1,
  "tpsa": 87.2,
  "rot_bonds": 5,
  "hbd": 2,
  "hba": 5,
  "flags": ["lipinski_mw_violation"],
  "filters_failed": [],
  "cluster_id": 2,
  "cluster_rank": 1,
  "overall_rank": 3
}
```

The `overall_rank` is determined by: confirmed affinity × ligand efficiency, descending, with filter failures pushing entries to the bottom.

---

## Selectivity Oracle

Selectivity is only meaningful after affinity is confirmed. The triage selectivity check:
1. Re-use the confirmed pose (already produced in stage 1)
2. Dock the same PDBQT against the off-target receptor
3. Compute `ratio = |target_confirmed_affinity| / |offtarget_best_affinity|`

The off-target receptor is configured in `targets/<name>/config.yaml` under `offtarget_receptor` and `offtarget_box`. If not configured, the selectivity stage is skipped with a note in the report.

---

## Pluggability

Like the docking backend, triage stages are pluggable. Each stage implements:
```
TriageStage:
  .name: str
  .analyze(finding: FindingMetadata, target_config: TargetConfig) -> TriageResult
```

The triage runner iterates stages in order. New stages (e.g., a machine learning ADMET model, or a protein-ligand interaction fingerprint tool) can be added without changing the runner.

---

## Build Criterion

Given a `runs/test/findings/` directory with at least one finding from a test run:

```
biofuzz-triage --findings runs/test/findings --target hiv_protease

Expected:
- runs/test/triage/report.json exists
- Each finding has confirmed_affinity, ligand_efficiency, flags populated
- Top N hits copied to runs/test/triage/top_hits/
- Run completes in < 5 minutes for 20 findings (exhaustiveness=16 on gnina)
```
