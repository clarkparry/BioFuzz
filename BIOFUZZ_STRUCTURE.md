# BioFuzz — Project Structure & Build Order

A developer's roadmap for building the fuzzer incrementally.
Each phase is independently testable before moving to the next.

---

## Repository Layout

```
BioFuzz/
│
├── biofuzz/                    # Main package
│   ├── __init__.py
│   │
│   ├── core/                   # The fuzzing engine
│   │   ├── __init__.py
│   │   ├── fuzzer.py           # Main loop orchestrator
│   │   ├── corpus.py           # Molecule queue / priority management
│   │   └── coverage.py         # Coverage map (the bitmap equivalent)
│   │
│   ├── molecules/              # Everything molecule-side
│   │   ├── __init__.py
│   │   ├── preparation.py      # SMILES → 3D → PDBQT pipeline
│   │   ├── mutator.py          # Mutation engine (fragment swap, R-group, etc.)
│   │   └── filters.py          # Drug-likeness validity gates
│   │
│   ├── docking/                # Docking engine interface
│   │   ├── __init__.py
│   │   ├── runner.py           # Subprocess wrapper for Vina/Gnina
│   │   ├── parser.py           # Parse PDBQT poses + log affinity scores
│   │   └── config.py           # Binding box, exhaustiveness, paths
│   │
│   ├── oracle/                 # Bug oracle — is this a hit?
│   │   ├── __init__.py
│   │   ├── affinity.py         # Tier 1: affinity threshold check
│   │   ├── selectivity.py      # Tier 2: off-target dock + ratio
│   │   └── strain.py           # Tier 3: internal strain energy filter
│   │
│   ├── protein/                # Protein-side utilities
│   │   ├── __init__.py
│   │   ├── residues.py         # Parse protein PDBQT → residue coords
│   │   └── pocket.py           # Pocket definition, contact fingerprinting
│   │
│   └── storage/                # Persistence
│       ├── __init__.py
│       ├── findings.py         # Save confirmed hits to disk
│       └── cache.py            # PDBQT prep cache (avoid re-preparing)
│
├── targets/                    # One directory per protein target
│   └── hiv_protease/
│       ├── protein.pdbqt       # Prepared protein file
│       ├── config.py           # Box coords, pocket residues, thresholds
│       └── reference_ligands/  # Known inhibitors for benchmarking
│           └── indinavir.smi
│
├── seeds/                      # Initial molecule corpus
│   └── zinc_druglike_10k.smi   # Downloaded from ZINC20
│
├── runs/                       # Output of each fuzzing session
│   └── 2025-01-01_hiv/
│       ├── findings/           # Confirmed hit PDBQT + metadata
│       ├── corpus/             # Evolved molecule queue state
│       └── coverage.json       # Coverage map snapshot
│
├── scripts/                    # Standalone utilities
│   ├── prep_protein.sh         # Wrap ADFRsuite prepare_receptor
│   ├── download_zinc.py        # Seed corpus downloader
│   └── visualize_hit.py        # Open a hit in PyMOL automatically
│
├── tests/                      # One test file per module
│   ├── test_preparation.py
│   ├── test_mutator.py
│   ├── test_parser.py
│   ├── test_coverage.py
│   └── test_oracle.py
│
├── config.yaml                 # Global defaults (overridden per target)
├── requirements.txt
└── main.py                     # Entrypoint: `python main.py --target hiv_protease`
```

---

## Build Phases

Build and test in this order. Each phase has a clear completion criterion — don't move forward until the phase works end-to-end.

```
Phase 1: Molecule Pipeline          SMILES → valid PDBQT on disk
    │
Phase 2: Docking Runner             PDBQT → affinity score + pose
    │
Phase 3: Output Parser              pose file → structured data in Python
    │
Phase 4: Coverage Map               pose data → residue contact fingerprint
    │
Phase 5: Oracle                     score + fingerprint → hit/no-hit decision
    │
Phase 6: Corpus Manager             prioritized queue + new-coverage tracking
    │
Phase 7: Mutation Engine            molecule → chemically valid mutants
    │
Phase 8: Main Loop                  wire phases 1-7 into a running fuzzer
    │
Phase 9: Selectivity Oracle         dock mutants against off-target protein
    │
Phase 10: Parallelism               multiprocessing across N workers
```

---

## Phase 1 — Molecule Pipeline
**Files:** `biofuzz/molecules/preparation.py`, `biofuzz/molecules/filters.py`

The preparation pipeline is the "input harness" of your fuzzer.
A molecule that fails prep is like a malformed input that crashes the harness before reaching the target — discard it.

```
SMILES string
    │
    ▼
[filters.py] is_drug_like()
    │   MW, LogP, HBD, HBA, RotBonds — fast rejections before expensive steps
    │   FAIL → discard
    ▼
[preparation.py] MolFromSmiles()
    │   Parse SMILES → RDKit mol object
    │   FAIL → discard (invalid SMILES)
    ▼
[preparation.py] AddHs() + EmbedMolecule()
    │   Add hydrogens, generate 3D conformer (ETKDGv3)
    │   FAIL (returns -1) → retry up to 3x with different random seeds → discard
    ▼
[preparation.py] MMFFOptimizeMolecule()
    │   Force field minimization — refine geometry
    ▼
[preparation.py] MoleculePreparation().prepare()
    │   Meeko: assign partial charges + atom types
    │   FAIL → discard
    ▼
PDBQT string / file on disk
```

**Completion criterion:**
```python
from biofuzz.molecules.preparation import prepare_smiles

pdbqt = prepare_smiles("CC(=O)Oc1ccccc1C(=O)O")  # aspirin
assert pdbqt is not None
assert "TORSDOF" in pdbqt  # Meeko always writes this line
print("Phase 1 OK")
```

---

## Phase 2 — Docking Runner
**Files:** `biofuzz/docking/runner.py`, `biofuzz/docking/config.py`

A thin subprocess wrapper around Vina/Gnina. Takes a PDBQT string (or file path), writes it to a temp file, runs the docking binary, returns stdout/stderr and output file paths.

This is deliberately a dumb wrapper — all intelligence lives in later phases.

```python
# biofuzz/docking/runner.py (interface)

@dataclass
class DockingResult:
    success:    bool
    log_text:   str        # raw Vina stdout/log
    pose_path:  str | None # path to output PDBQT
    error:      str | None

def dock(
    ligand_pdbqt:  str,        # PDBQT content as string
    receptor_path: str,        # path to prepared protein PDBQT
    box:           BoxConfig,  # center_x/y/z, size_x/y/z
    exhaustiveness: int = 4,
    num_modes:     int = 3,
) -> DockingResult:
    ...
```

Key implementation detail: use `tempfile.NamedTemporaryFile` for the ligand PDBQT so you don't accumulate files on disk during fuzzing.

**Completion criterion:**
```bash
# From a Python shell:
from biofuzz.docking.runner import dock
from biofuzz.docking.config import load_target_config

cfg    = load_target_config("hiv_protease")
result = dock(open("targets/hiv_protease/reference_ligands/indinavir.pdbqt").read(), cfg)
assert result.success
print("Phase 2 OK")
```

---

## Phase 3 — Output Parser
**Files:** `biofuzz/docking/parser.py`

Parse the raw text output from Vina/Gnina into structured Python objects.
Two things to parse: the log file (affinity scores) and the pose PDBQT (3D atom coordinates).

```python
# biofuzz/docking/parser.py (interface)

@dataclass
class DockingMode:
    mode:      int
    affinity:  float      # kcal/mol, negative = good
    rmsd_lb:   float
    rmsd_ub:   float

@dataclass
class PoseAtom:
    name:   str
    x:      float
    y:      float
    z:      float
    charge: float
    type:   str           # Vina atom type (C, OA, HD, etc.)

def parse_log(log_text: str)     -> list[DockingMode]: ...
def parse_pose(pdbqt_text: str)  -> list[PoseAtom]:    ...  # best pose only
```

**Completion criterion:**
```python
modes = parse_log(result.log_text)
assert modes[0].affinity < -9.0     # indinavir should score well
assert len(modes) >= 1
atoms = parse_pose(open(result.pose_path).read())
assert len(atoms) > 0
assert all(hasattr(a, 'x') for a in atoms)
print("Phase 3 OK")
```

---

## Phase 4 — Coverage Map
**Files:** `biofuzz/protein/residues.py`, `biofuzz/protein/pocket.py`, `biofuzz/core/coverage.py`

The coverage subsystem. Analogous to AFL's `__afl_area_ptr` bitmap.

```
protein/residues.py     Parse protein PDBQT → {res_id: [(x,y,z), ...]}
         │
         ▼
protein/pocket.py       For a given pose, compute which pocket residues
                        are within CONTACT_CUTOFF Å of any ligand atom
                        → frozenset of residue IDs (the "fingerprint")
         │
         ▼
core/coverage.py        CoverageMap class:
                          - global_coverage: set  (union of all fingerprints seen)
                          - update(fingerprint) → new_bits (empty = no new coverage)
                          - save/load to JSON    (resume across runs)
```

```python
# core/coverage.py (interface)

class CoverageMap:
    def update(self, fingerprint: frozenset) -> frozenset:
        """Returns newly covered residues. Empty = no new coverage."""

    def coverage_ratio(self) -> float:
        """Fraction of pocket residues ever contacted. 1.0 = full coverage."""

    def save(self, path: str) -> None: ...
    def load(self, path: str) -> None: ...
```

**Completion criterion:**
```python
from biofuzz.protein.pocket import compute_fingerprint
from biofuzz.core.coverage import CoverageMap

fp1  = compute_fingerprint(atoms_indinavir, protein_residues, pocket_def)
fp2  = compute_fingerprint(atoms_aspirin,   protein_residues, pocket_def)

cov  = CoverageMap(pocket_def.residue_ids)
new1 = cov.update(fp1)   # all of fp1 is new
new2 = cov.update(fp2)   # only residues in fp2 NOT in fp1 are new
new3 = cov.update(fp1)   # empty -- fp1 was already seen

assert len(new1) > 0
assert new3 == frozenset()
print("Phase 4 OK")
```

---

## Phase 5 — Oracle
**Files:** `biofuzz/oracle/affinity.py`, `biofuzz/oracle/strain.py`

The oracle decides whether a docking result is a "crash" — worth saving.
Keep each tier as a separate function so you can tune and combine them independently.

```python
# biofuzz/oracle/ (interface)

def passes_affinity(modes: list[DockingMode], threshold: float = -9.0) -> bool:
    """Tier 1: best affinity below threshold."""
    return bool(modes) and modes[0].affinity <= threshold

def passes_strain(pose_pdbqt: str, threshold: float = 3.5) -> bool:
    """Tier 3: internal strain energy acceptable.
    Gnina reports this in the REMARK lines of the output PDBQT."""
    ...

@dataclass
class OracleVerdict:
    is_hit:       bool
    affinity:     float
    passed_tiers: list[str]   # ["affinity", "strain"] etc.
    notes:        str

def evaluate(modes, pose_pdbqt, config) -> OracleVerdict: ...
```

Note: the selectivity oracle (Tier 2) lives in `oracle/selectivity.py` but is built in Phase 9 — it requires a second docking run and you don't want that blocking the main loop during early development.

**Completion criterion:**
```python
verdict = evaluate(modes_indinavir, pose_pdbqt_indinavir, cfg)
assert verdict.is_hit           # indinavir should be a confirmed hit
assert verdict.affinity < -9.0
print("Phase 5 OK")
```

---

## Phase 6 — Corpus Manager
**Files:** `biofuzz/core/corpus.py`

The molecule queue. Analogous to AFL's `queue/` directory + prioritization logic.
Molecules with new coverage get higher priority. Molecules that produced no new coverage get lower priority but aren't discarded (they're still useful for splicing).

```python
# biofuzz/core/corpus.py (interface)

@dataclass
class CorpusEntry:
    smiles:        str
    source_id:     str          # ZINC ID or "mutant_of:<parent_smiles>"
    priority:      float        # higher = mutated sooner
    times_mutated: int
    best_affinity: float | None
    new_bits:      int          # how many new coverage bits this produced

class Corpus:
    def add(self, entry: CorpusEntry) -> None: ...
    def pop(self) -> CorpusEntry: ...          # returns highest priority entry
    def size(self) -> int: ...
    def save(self, path: str) -> None: ...     # checkpoint to disk
    def load(self, path: str) -> None: ...     # resume from checkpoint
```

**Priority formula** (tune this):
```python
priority = (new_bits * 10.0) + max(0, -affinity - 5.0) - (times_mutated * 0.1)
# New coverage dominates. Affinity bonus. Slight penalty for overused entries.
```

**Completion criterion:**
```python
corpus = Corpus()
corpus.add(CorpusEntry("CCO", "test", priority=1.0, ...))
corpus.add(CorpusEntry("c1ccccc1", "test2", priority=5.0, ...))
entry = corpus.pop()
assert entry.smiles == "c1ccccc1"   # higher priority comes out first
print("Phase 6 OK")
```

---

## Phase 7 — Mutation Engine
**Files:** `biofuzz/molecules/mutator.py`

The mutation engine takes a SMILES and returns N mutated SMILES strings.
Each mutation is a graph operation on the RDKit molecule object.
Every mutant must pass the validity filter before being returned.

```python
# biofuzz/molecules/mutator.py (interface)

def mutate(smiles: str, n: int = 20) -> list[str]:
    """
    Generate up to N valid mutants of the input molecule.
    Returns fewer than N if mutations keep failing validity.
    Each returned SMILES is guaranteed to pass is_drug_like().
    """
    ...
```

**Mutation operations to implement, in order of difficulty:**

```
1. atom_type_swap()         Easy    Swap C→N, C→O, S→O at a random position
2. add_substituent()        Easy    Attach -F, -Cl, -CH3, -OH to an aromatic ring
3. remove_substituent()     Easy    Remove a non-ring substituent
4. linker_extension()       Medium  Extend a chain by one CH2
5. linker_contraction()     Medium  Remove one CH2 from a chain
6. ring_atom_swap()         Medium  Swap a ring carbon for N (pyridine-ification)
7. fragment_replacement()   Hard    Replace a substructure with a bioisostere
8. splice()                 Hard    Combine scaffold of A with substituent of B
```

Start with 1–3. They're sufficient for a working fuzzer. Add 4–8 incrementally.

**Completion criterion:**
```python
from biofuzz.molecules.mutator import mutate

mutants = mutate("c1ccc(O)cc1", n=20)   # phenol
assert len(mutants) >= 5                 # at least some valid mutants
assert all(Chem.MolFromSmiles(m) for m in mutants)  # all parse
assert "c1ccc(O)cc1" not in mutants     # not just returning the input
print("Phase 7 OK")
```

---

## Phase 8 — Main Loop
**Files:** `biofuzz/core/fuzzer.py`, `main.py`

Wire everything together. This is the core feedback loop.

```python
# biofuzz/core/fuzzer.py

def run(target_config, seed_smiles_path, output_dir, max_iterations=None):

    # --- Init ---
    corpus   = Corpus()
    cov_map  = CoverageMap(target_config.pocket_residues)
    findings = FindingsStore(output_dir / "findings")
    cache    = PDBQTCache(output_dir / "cache")

    # Load seeds into corpus at baseline priority
    for smiles, zinc_id in load_smiles(seed_smiles_path):
        corpus.add(CorpusEntry(smiles, zinc_id, priority=1.0, ...))

    # --- Main loop ---
    iteration = 0
    while True:
        if max_iterations and iteration >= max_iterations:
            break

        # 1. Dequeue
        entry = corpus.pop()

        # 2. Mutate
        mutants = mutate(entry.smiles, n=20)

        for smiles in mutants:
            iteration += 1

            # 3. Prepare
            pdbqt = cache.get(smiles) or prepare_smiles(smiles)
            if pdbqt is None:
                continue                     # preparation failed -- discard
            cache.set(smiles, pdbqt)

            # 4. Dock
            result = dock(pdbqt, target_config)
            if not result.success:
                continue

            # 5. Parse
            modes = parse_log(result.log_text)
            atoms = parse_pose(open(result.pose_path).read())
            if not modes or not atoms:
                continue

            # 6. Coverage
            fingerprint  = compute_fingerprint(atoms, protein_residues,
                                               target_config.pocket)
            new_bits     = cov_map.update(fingerprint)
            affinity     = modes[0].affinity

            # 7. Oracle
            verdict = evaluate(modes, result.pose_path, target_config)
            if verdict.is_hit:
                findings.save(smiles, verdict, result.pose_path)
                log(f"[HIT] {smiles} | affinity={affinity:.1f}")

            # 8. Corpus update
            priority = compute_priority(new_bits, affinity, entry.times_mutated)
            corpus.add(CorpusEntry(smiles, f"mutant_of:{entry.source_id}",
                                   priority=priority, best_affinity=affinity,
                                   new_bits=len(new_bits)))

            # 9. Log progress
            log_progress(iteration, cov_map, corpus, findings)

        # Checkpoint periodically
        if iteration % 500 == 0:
            cov_map.save(output_dir / "coverage.json")
            corpus.save(output_dir / "corpus.json")
```

**Completion criterion:**
Run for 50 iterations on HIV protease. Verify:
- At least one molecule scores better than −8 kcal/mol
- Coverage map grows over time
- No crashes, no hung processes
- Findings directory contains at least one entry

---

## Phase 9 — Selectivity Oracle
**Files:** `biofuzz/oracle/selectivity.py`

Add off-target docking. Only run selectivity checks on molecules that already passed the affinity oracle — it's expensive (a second full docking run).

```python
# biofuzz/oracle/selectivity.py

def selectivity_ratio(
    smiles:        str,
    target_config: TargetConfig,
    offtarget_config: TargetConfig,
) -> float:
    """
    Dock against both target and off-target.
    Returns target_affinity / offtarget_affinity.
    Higher ratio = more selective.
    Affinities are negative, so this is actually:
        abs(target_affinity) / abs(offtarget_affinity)
    """
    target_result    = dock(smiles, target_config,    exhaustiveness=8)
    offtarget_result = dock(smiles, offtarget_config, exhaustiveness=8)
    ...
```

Integrate into `oracle/affinity.py`'s `evaluate()` so the main loop doesn't change.

---

## Phase 10 — Parallelism
**Files:** `biofuzz/core/fuzzer.py` (modification)

Docking is embarrassingly parallel — each molecule is independent. Use Python's `multiprocessing.Pool` to run N docking jobs simultaneously.

```python
from multiprocessing import Pool

# Replace the inner mutant loop with:
with Pool(processes=NUM_WORKERS) as pool:
    dock_jobs = [(pdbqt, target_config) for pdbqt in prepared_mutants]
    results   = pool.starmap(dock, dock_jobs)

# Process results sequentially (coverage map + corpus are not thread-safe)
for smiles, result in zip(mutant_smiles, results):
    modes       = parse_log(result.log_text)
    fingerprint = compute_fingerprint(...)
    ...
```

**Important**: the coverage map and corpus must be updated sequentially, even though docking runs are parallel. Coverage map writes are not thread-safe — don't try to update it from worker processes.

---

## Data Flow Summary

```
zinc_seeds.smi
      │
      ▼
  Corpus (priority queue)
      │
      │◄──────────────────────────────────────────┐
      ▼                                           │
  pop() → entry.smiles                            │
      │                                           │
      ▼                                           │
  mutate() → [smiles_1, smiles_2, ..., smiles_N]  │
      │                                           │
      ▼  (for each mutant)                        │
  prepare_smiles()                                │
      │ fail → discard                            │
      ▼                                           │
  dock()                                          │
      │                                           │
      ├──── parse_log() ──────► affinity ──► oracle ──► findings/
      │                                           │
      ├──── parse_pose()                          │
      │          │                                │
      │          ▼                                │
      │    compute_fingerprint()                  │
      │          │                                │
      │          ▼                                │
      │    coverage_map.update()                  │
      │          │                                │
      │          └──── new_bits                   │
      │                    │                      │
      └────────────────────┴──► compute_priority()│
                                        │         │
                                        ▼         │
                                  corpus.add() ───┘
```

---

## Config Schema (config.yaml)

```yaml
# Global defaults -- overridden per target
docking:
  exhaustiveness_fuzz:    4      # during fuzzing loop (fast)
  exhaustiveness_confirm: 16     # re-dock confirmed hits
  num_modes:              3
  engine:                 gnina  # or: vina, quickvina

oracle:
  affinity_threshold:    -9.0    # kcal/mol
  strain_threshold:       3.5    # kcal/mol internal strain max
  selectivity_ratio_min:  2.0    # target/offtarget ratio minimum

molecules:
  max_mw:         550
  max_logp:         5.0
  max_rot_bonds:   10
  mutations_per_entry: 20

corpus:
  max_size:       50000
  priority_new_bit_weight:  10.0
  priority_affinity_weight:  1.0
  priority_reuse_penalty:    0.1

fuzzer:
  workers:         4             # parallel docking processes
  checkpoint_every: 500          # iterations between corpus/coverage saves
  log_level:       INFO
```

---

## Target Config Schema (targets/hiv_protease/config.py)

```python
from biofuzz.docking.config import TargetConfig, BoxConfig, PocketConfig

HIV_PROTEASE = TargetConfig(
    name         = "hiv_protease",
    receptor     = "targets/hiv_protease/protein.pdbqt",

    box = BoxConfig(
        center_x = 2.5,
        center_y = 8.0,
        center_z = 12.0,
        size_x   = 20.0,
        size_y   = 20.0,
        size_z   = 20.0,
    ),

    pocket = PocketConfig(
        # Residue IDs within ~5Å of active site -- identified via PyMOL
        residue_ids = {8,23,25,26,27,28,29,30,32,45,46,47,48,49,
                       50,51,52,53,54,76,80,81,82,84},
        contact_cutoff = 3.5,   # Angstroms
    ),

    oracle = OracleConfig(
        affinity_threshold = -9.0,
        strain_threshold   =  3.5,
    ),

    # Optional: off-target for selectivity check
    offtarget_receptor = "targets/human_pepsin/protein.pdbqt",
    offtarget_box      = BoxConfig(...),
)
```

---

## Testing Strategy

Each module has a test file. Use `pytest`.

```bash
# Run all tests
pytest tests/ -v

# Run a specific phase's tests
pytest tests/test_mutator.py -v

# Run with coverage report
pytest tests/ --cov=biofuzz --cov-report=term-missing
```

**What to test:**

| Module | Test cases |
|---|---|
| `filters.py` | aspirin passes; known toxic compound fails; edge cases at MW boundary |
| `preparation.py` | valid SMILES → PDBQT; invalid SMILES → None; embedding failure → None |
| `parser.py` | parse real Vina log; parse real pose PDBQT; handle empty output |
| `coverage.py` | update adds bits; re-update same fp → empty new_bits; save/load round-trip |
| `oracle.py` | strong binder → hit; weak binder → no hit; strain too high → no hit |
| `corpus.py` | pop returns highest priority; save/load preserves order |
| `mutator.py` | all outputs parse; no output = input; n=0 → empty list |

---

## Milestone Checklist

- [ ] **Phase 1**: `prepare_smiles("aspirin_smiles")` returns a PDBQT string
- [ ] **Phase 2**: Docking indinavir against 1HVR completes without error
- [ ] **Phase 3**: Parser extracts affinity ≈ −11 kcal/mol from indinavir run
- [ ] **Phase 4**: Indinavir fingerprint contacts ≥ 10 pocket residues
- [ ] **Phase 5**: Indinavir passes the oracle; a random drug-like molecule likely doesn't
- [ ] **Phase 6**: Corpus pops entries in priority order; save/load works
- [ ] **Phase 7**: `mutate("phenol")` returns ≥ 5 valid, distinct SMILES
- [ ] **Phase 8**: 50-iteration run completes; coverage grows; no crashes
- [ ] **Phase 9**: Selectivity check runs on oracle hits; ratio computed correctly
- [ ] **Phase 10**: 4-worker run is ~3x faster than 1-worker run on same molecules
