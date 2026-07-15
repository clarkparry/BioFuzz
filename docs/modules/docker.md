# Module: Docking Backend

**AFL++ analog:** The target binary — what gets "executed" on each input

The docking backend is the "program under test." It takes a prepared ligand and protein, runs a docking simulation, and returns an affinity score plus a pose file. The module defines an abstract interface so the engine can be swapped without changing the fuzzer.

**Default and primary implementation: gnina.**
gnina is preferred over Vina because its CNN-based scoring function (trained on PDBbind) is meaningfully more accurate for most binding site geometries. GPU support also makes gnina ~10–15× faster than CPU Vina at comparable exhaustiveness.

---

## Boundary

**Does:**
- Accept a ligand PDBQT string + target config and return a `DockingResult`
- Handle tempfile creation and cleanup for ligand and pose files
- Handle timeouts, subprocess errors, and GPU detection
- Resolve the docking binary from PATH or local bin directory

**Does not:**
- Parse affinity scores or poses from the result (Parser module)
- Generate or validate the PDBQT (Prep module)
- Decide whether a result is a hit (Oracle)
- Know anything about coverage

---

## Abstract Interface

All docking backends implement this interface. The fuzzer imports only this contract.

```
DockingBackend:
  .dock(ligand_pdbqt: str, config: DockingConfig) -> DockingResult
  .name: str                  # "gnina", "vina", etc.
  .available() -> bool        # can the binary be found and executed?
  .runtime_issues() -> list[str]  # missing libraries, GPU unavailable, etc.
```

```
DockingConfig:
  receptor_path: str
  center_x: float
  center_y: float
  center_z: float
  size_x: float
  size_y: float
  size_z: float
  exhaustiveness: int
  num_modes: int
  timeout_seconds: int

DockingResult:
  success: bool
  log_text: str         # raw stdout/stderr from docking binary
  pose_path: str | None # path to output PDBQT (caller owns cleanup)
  error: str | None
  completed: bool       # True if the binary exited normally (even if score poor)
  gpu_active: bool | None
```

---

## Gnina Implementation

Gnina is the bundled default. It is invoked as a subprocess with the following key flags:

```
gnina
  --receptor  <receptor.pdbqt>
  --ligand    <ligand_tmp.pdbqt>
  --center_x  <x>  --center_y  <y>  --center_z  <z>
  --size_x    <sx> --size_y   <sy>  --size_z   <sz>
  --exhaustiveness  <n>
  --num_modes  <m>
  --cpu  1           # only when workers > 1 (see below)
  --out  <pose_tmp.pdbqt>
```

**Important:** `--cpu 1` is only passed when the fuzzer is running multiple worker processes. If `workers=1`, omit this flag and let gnina use all available CPU threads internally. Passing `--cpu 1` on a single-worker run needlessly constrains gnina.

**GPU detection:** inferred from gnina's log output. If the log contains `"warning: no gpu detected"` or `"--no_gpu"`, `gpu_active` is set to False. If the run completed successfully and no GPU warning appears, `gpu_active` is True.

**Binary resolution order:**
1. Explicit `--engine` path from config (if absolute or in PATH)
2. System PATH: `gnina`
3. Repo-local: `.tools/bin/gnina`

No fallback to vina or other engines is attempted. If gnina is unavailable, the fuzzer exits with a clear error message.

---

## Tempfile Hygiene

The ligand PDBQT is written to a named tempfile, passed to gnina, and deleted in a `finally` block — even on timeout. The pose output file is left on disk and its path returned in `DockingResult.pose_path`. The caller (fuzzer) is responsible for cleaning up the pose file after processing.

This is important in a multi-worker context: workers create pose tempfiles in their own process space. The main process deletes them by path after reading their contents.

---

## Output Parser

Parsing the raw log_text and pose PDBQT into structured data is the responsibility of a thin `Parser` sub-module within `docker/`:

```
parse_log(log_text: str) -> list[DockingMode]
parse_pose(pdbqt_text: str) -> list[PoseAtom]

DockingMode:
  mode: int
  affinity: float      # kcal/mol, negative = favorable
  rmsd_lb: float
  rmsd_ub: float

PoseAtom:
  name: str
  x: float
  y: float
  z: float
  charge: float
  type: str            # Vina atom type (C, OA, HD, etc.)
```

`parse_pose` returns atoms from the **best pose only** (first MODEL block). Only heavy atoms (non-H) should be returned for coverage fingerprinting.

---

## Adding a New Backend

To add a new docking engine:
1. Create a class implementing the `DockingBackend` interface in `biofuzz/docker/`
2. Register it in `biofuzz/docker/__init__.py` under a string name
3. Set `docking.engine: <name>` in config.yaml

No other module changes are needed.

---

## Build Criterion

```python
from biofuzz.docker.gnina import GninaBackend
from biofuzz.docker.parser import parse_log, parse_pose

backend = GninaBackend()
assert backend.available(), "gnina binary not found"
assert backend.runtime_issues() == [], "gnina has runtime issues"

config = DockingConfig(
    receptor_path="targets/hiv_protease/protein.pdbqt",
    center_x=2.5, center_y=8.0, center_z=12.0,
    size_x=20.0, size_y=20.0, size_z=20.0,
    exhaustiveness=4, num_modes=3, timeout_seconds=120,
)
indinavir_pdbqt = open("seeds/per_target/hiv_protease/indinavir.pdbqt").read()

result = backend.dock(indinavir_pdbqt, config)
assert result.success
assert result.pose_path is not None

modes = parse_log(result.log_text)
assert modes[0].affinity < -8.0   # indinavir should score well

atoms = parse_pose(open(result.pose_path).read())
assert len(atoms) > 0
assert all(not a.type.upper().startswith("H") for a in atoms)  # heavy atoms only
```
