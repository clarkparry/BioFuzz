# Module: Preparation

**AFL++ analog:** Input format conversion / harness instrumentation wrapper

The preparation pipeline converts a SMILES string into a PDBQT file suitable for docking. It is the "input harness" of BioFuzz — a molecule that fails preparation is like a malformed input that crashes the harness before reaching the target. Discard it cleanly and move on.

---

## Boundary

**Does:**
- Validate drug-likeness before expensive steps (fast fail)
- Generate a 3D conformer from SMILES using RDKit
- Convert the 3D conformer to PDBQT format using Meeko
- Cache the prepared PDBQT by canonical SMILES
- Return `None` on any failure — never a fallback or partial result

**Does not:**
- Apply mutations (Mutator)
- Cache to disk (Storage/Cache module)
- Know anything about the target or docking
- Implement a fallback PDBQT generator

There is no fallback PDBQT preparer. A rigid PDBQT with no rotatable bond records is worse than no PDBQT — it produces misleading docking scores that poison the corpus. If Meeko fails, return `None`.

---

## Pipeline

```
SMILES string
    │
    ▼
[1] Drug-likeness filter (fast)
    │   MW, logP, HBD, HBA, rotatable bonds
    │   Disconnected fragments check
    │   FAIL → return None
    ▼
[2] RDKit MolFromSmiles()
    │   FAIL → return None (invalid SMILES)
    ▼
[3] AddHs() + ETKDGv3 embedding
    │   Retry up to 3× with different random seeds
    │   All retries fail → return None
    ▼
[4] MMFF geometry optimization
    │   Falls back to UFF if MMFF params unavailable
    │   Failure here is non-fatal (embedding alone is sufficient)
    ▼
[5] Meeko MoleculePreparation.prepare()
    │   Assigns partial charges, identifies rotatable bonds, builds BRANCH tree
    │   FAIL → return None
    ▼
PDBQT string
```

The output PDBQT must carry a `TORSDOF` record, plus `BRANCH`/`ENDBRANCH`
records whenever `TORSDOF` is non-zero. `_is_valid_pdbqt` checks this before
returning. A PDBQT missing its torsion tree is a rigid ligand in disguise:
docking it yields a plausible-looking score for a molecule that was never allowed
to flex.

---

## Drug-Likeness Filter

Filter parameters are passed by the caller from config — the preparation module does not hardcode thresholds. This makes the filter tunable at the campaign level without module changes.

Parameters:
- `min_mw: float` — minimum molecular weight (Da)
- `max_mw: float` — maximum molecular weight (Da)
- `max_logp: float` — maximum lipophilicity
- `max_hbd: int` — maximum hydrogen bond donors
- `max_hba: int` — maximum hydrogen bond acceptors
- `max_rot_bonds: int` — maximum rotatable bonds
- Connected graph (single fragment): always enforced

The filter uses RDKit descriptors. It is fast and should run before any 3D operation.

---

## Interface

```
prepare_smiles(
    smiles: str,
    min_mw: float,
    max_mw: float,
    max_logp: float,
    max_hbd: int,
    max_hba: int,
    max_rot_bonds: int,
    retries: int = 3,
) -> str | None      # PDBQT string, or None on failure
```

The bounds are **required**, with no defaults. That is deliberate: a default MW
cap applied silently inside the preparation step is exactly how a target's own
chemical class gets excluded with nothing logged. The caller must state the
envelope it wants.

Same input always produces semantically equivalent output — conformer geometry
varies with the embedding seed, but the PDBQT encoding is equally valid. The cache
layer wraps this function; the preparation module itself has no caching.

---

## Pluggability

Preparation is **not** selected by config, unlike the docking backend and the
UI. There is one implementation, behind the `prepare_smiles` function contract.
Substituting another preparer — a wrapper around ADFRsuite's
`prepare_ligand4.py`, say — means providing a function with that signature and
the same failure contract (`None`, never a partial result), not registering a
name in `config.yaml`.

The contract is the part worth keeping stable: the fuzzer and triage both depend
on "PDBQT string or None".

---

## Performance Notes

Preparation takes 1–5 seconds per molecule on a modern CPU. It is the second-slowest step after docking. To keep it off the critical path:

- Preparation runs **in the same worker pool as docking** (`prepare_worker`).
  Embedding and optimisation are pure CPU and independent per molecule, so
  running them on the main process would leave the pool idle during the second
  largest cost in the loop.
- The PDBQT cache (Storage module) avoids re-preparing molecules seen in prior
  runs, keyed on canonical SMILES.
- Drug-likeness filtering runs before any 3D step. Most mutants fail the filter
  quickly, which is the desired behaviour.

---

## Verification

`tests/test_prep.py` covers the pipeline: a drug-like molecule yielding a PDBQT
with a valid torsion tree, invalid SMILES returning `None`, property bounds
rejecting an over-weight molecule, stable encoding across repeated calls, and the
UFF fallback via phenylboronic acid, which has no MMFF parameters.

`tests/test_calibration.py` additionally asserts that each bundled target's own
`molecules:` bounds can prepare that target's reference drug — the guard against
an envelope that would silently exclude the target's chemical class.
