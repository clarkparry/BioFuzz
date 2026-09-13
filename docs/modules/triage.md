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
    Strain from the confirmation dock's `intramol` column (NOT a pose REMARK --
      gnina does not emit one; see docker.md)
    Pose reproducibility: geometric RMSD spread between modes 1 and 2,
      computed from parse_all_poses because the CNN table reports no RMSD
    Filter: strain > threshold → downrank
    Flag: RMSD spread > 2.0 Å → pose may not be well-defined

    ▼
[3] Ligand Efficiency
    LE = -affinity / heavy_atom_count   (the oracle's definition, sign intact)
    Flag: LE < 0.3 → molecule may be too large for its binding quality

    Note the 0.3 here is an advisory flag, deliberately stricter than the
    oracle's 0.22 hard gate: in triage a flag costs nothing, so it can be set
    at the conventional lead-like threshold.

    Must run AFTER [4]: heavy_atom_count is computed by the ADMET stage.

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
    selectivity_ddg = offtarget_affinity - confirmed_affinity   (kcal/mol,
      positive = prefers the on-target)
    selectivity_fold = 10 ** (ddg / 1.364)   -- the Kd ratio a chemist quotes
    Flag: ddg < selectivity_min_ddg (default 1.4 kcal/mol, ~10x)
    Only runs if offtarget_receptor AND offtarget_box are configured.

    Selectivity is a DIFFERENCE of binding free energies, never a ratio of
    them. Both numbers are kcal/mol on a log scale, so dividing -10 by -5 to
    claim "2x selective" is a unit error: the real gap is 5 kcal/mol, roughly
    4000x in Kd.

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
  "selectivity_ddg": 2.3,
  "selectivity_fold": 48.2,
  "offtarget_affinity": -7.8,
  "pose_rmsd_spread": 0.8,
  "vina_affinity": -10.1,
  "cnn_affinity_kcal": -11.4,
  "cnn_pose_score": 0.87,
  "score_disagreement": 1.3,
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

`overall_rank` sorts on `max(0, -confirmed_affinity) * ligand_efficiency`,
descending, with filter failures pushed to the bottom. Both terms keep their
sign, so a molecule whose confirmed score came back unfavourable cannot rank
above a real binder.

The per-function columns (`vina_affinity`, `cnn_affinity_kcal`, `cnn_pose_score`,
`score_disagreement`) are recorded so a report says *why* something ranked where
it did, not merely that it did.

---

## Selectivity Oracle

Selectivity is only meaningful after affinity is confirmed. The stage:
1. Re-prepares the molecule under structural-validity-only bounds
2. Docks it against the off-target receptor at the confirmation exhaustiveness
3. Computes `ddg = offtarget_best - confirmed_on_target`, under the same scoring
   policy used on the on-target, and the implied Kd fold-difference

The off-target receptor is configured in `targets/<name>/config.yaml` under
`offtarget_receptor` and `offtarget_box`. If either is absent the stage is
skipped and flags `selectivity_not_configured`.

**No bundled target configures an off-target**, so this stage has only ever run
in its skip path. Treat it as untested against real data.

---

## Pluggability

Triage stages are pluggable. Each stage implements:
```
TriageStage:
  .name: str
  .analyze(record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult

TriageStageResult:
  fields: dict            # merged onto the record (unknown keys ignored)
  flags: list[str]        # advisory
  filter_failed: str | None   # downranks the finding
```

The runner (`default_stages()` + `run_triage()`) iterates the list in order,
merging each result onto a mutable `TriageRecord`. New stages — a machine-learning
ADMET model, a protein-ligand interaction fingerprint tool — can be added without
changing the runner.

**Stage order is load-bearing**, because stages communicate through the record.
`ADMETStage` must precede `LigandEfficiencyStage`, which needs the
`heavy_atom_count` that ADMET computes; `ConfirmationDockingStage` must precede
anything reading `confirmed_affinity`. A regression test asserts the ADMET
ordering directly, since unit tests that build a pre-populated record cannot
catch it.

---

## Verification

`tests/test_triage.py` covers the stages in isolation — ADMET flags, chemistry
flags, pose quality, clustering, report writing — plus the ordering constraint
above and the ligand-efficiency sign convention. The live-docking stages
(confirmation, selectivity) are exercised only through their skip paths in the
suite, to keep its runtime reasonable.

For an end-to-end check, run a short campaign, then:

```sh
./biofuzz-triage --findings runs/<stamp>_<target>/findings --target <target>
```

Expect `report.json` with `confirmed_affinity`, `ligand_efficiency` and `flags`
populated on every record, a `report.html`, and `top_hits/` populated up to
`--top-n`. Confirmation re-docks at exhaustiveness 16, so budget roughly a minute
per finding on CPU.
