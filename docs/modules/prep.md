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

The output PDBQT must contain `TORSDOF` and valid `BRANCH`/`ENDBRANCH` records. Verify this before returning.

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

This is a pure function: same input always produces semantically equivalent output (conformer geometry may vary but the PDBQT encoding is equally valid). The cache layer wraps this function — the preparation module itself has no caching.

---

## Pluggability

The preparation backend is identified in config by name (`prep.backend: meeko`). To add a new backend (e.g., a wrapper around ADFRsuite's `prepare_ligand4.py`), implement the `prepare_smiles` interface and register it. The fuzzer calls only the interface; it does not import a specific backend directly.

---

## Performance Notes

Preparation takes 1–5 seconds per molecule on a modern CPU. It is the second-slowest step after docking. To keep it off the critical path:

- Run preparation in the main thread before dispatching dock jobs to workers. Workers receive ready PDBQT strings, not SMILES.
- The PDBQT cache (Storage module) avoids re-preparing molecules seen in prior runs.
- Drug-likeness filtering runs before any 3D step. Most mutants will fail the filter quickly — this is the desired behavior.

---

## Build Criterion

```python
from biofuzz.prep import prepare_smiles

# Valid drug-like molecule → PDBQT
pdbqt = prepare_smiles("CC(=O)Oc1ccccc1C(=O)O")   # aspirin
assert pdbqt is not None
assert "TORSDOF" in pdbqt
assert "BRANCH" in pdbqt or int(pdbqt.split("TORSDOF")[1].strip()) == 0

# Invalid SMILES → None
assert prepare_smiles("this is not smiles") is None

# Molecule failing drug-likeness → None
# e.g., a peptide with MW > 550
assert prepare_smiles("ACDEFGHIKLMNPQRSTVWY", max_mw=550.0) is None

# Repeated call → same TORSDOF (stable encoding)
pdbqt2 = prepare_smiles("CC(=O)Oc1ccccc1C(=O)O")
assert pdbqt.split("TORSDOF")[1] == pdbqt2.split("TORSDOF")[1]
```
