# Module: Docking Backend

**AFL++ analog:** The target binary — what gets "executed" on each input

The docking backend is the "program under test." It takes a prepared ligand and protein, runs a docking simulation, and returns an affinity score plus a pose file. The module defines an abstract interface so the engine can be swapped without changing the fuzzer.

**Default and primary implementation: gnina.**
gnina is preferred over Vina because it returns a second, independent estimate
of binding from a CNN trained on crystallographic complexes: a predicted affinity
*and* a pose-plausibility score. That second opinion is what the oracle's
consensus policy and pose-confidence tier are built on, and vina's empirical
function cannot supply it. gnina can use a GPU when one is present; without one
it still runs, taking tens of seconds per dock rather than a few.

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
  workers: int = 1            # whether to constrain the engine to one thread
  engine_path: str | None     # explicit binary path, overriding resolution
  cnn_model: str | None       # gnina --cnn model, or None for its default

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

**GPU detection** is inferred from the log, and only one direction of the
inference is sound. gnina announces a *missing* GPU ("WARNING: No GPU detected.")
but says nothing when one is present, so `gpu_active` is False on that warning,
True when a real log lacks it, and **None when there is no log to read** — an
empty or truncated log carries no evidence either way and must not be reported as
a GPU run.

**Binary resolution order:**
1. `DockingConfig.engine_path`, when set (a per-call override)
2. System PATH: `gnina`
3. Repo-local: `.tools/bin/gnina`

Note that `docking.engine` in `config.yaml` is a different thing: it names *which
backend class* to use, resolved through `get_backend()`, not where a binary lives.

No fallback to vina or another engine is attempted. If gnina cannot be resolved,
`dock()` returns an unsuccessful `DockingResult` rather than raising, and
`runtime_issues()` explains why.

---

## Tempfile Hygiene

The ligand PDBQT is written to a named tempfile, passed to gnina, and deleted in a `finally` block — even on timeout. The pose output file is left on disk and its path returned in `DockingResult.pose_path`. The caller (fuzzer) is responsible for cleaning up the pose file after processing.

This is important in a multi-worker context: workers create pose tempfiles in their own process space. The main process deletes them by path after reading their contents.

---

## Output Parser

Parsing the raw log_text and pose PDBQT into structured data is the responsibility of a thin `Parser` sub-module within `docker/`:

```
parse_log(log_text: str) -> list[DockingMode]
parse_pose(pdbqt_text: str) -> list[PoseAtom]          # first MODEL block
parse_all_poses(pdbqt_text: str) -> list[list[PoseAtom]]

DockingMode:
  mode: int
  affinity: float            # vina's empirical score, kcal/mol (negative = better)
  rmsd_lb: float = 0.0       # only populated in the non-CNN table layout
  rmsd_ub: float = 0.0
  intramol: float | None     # intramolecular (strain) energy, kcal/mol
  cnn_pose_score: float | None   # CNN pose plausibility, 0-1
  cnn_affinity: float | None     # CNN-predicted affinity, pKd (higher = better)

PoseAtom:
  name: str
  x: float
  y: float
  z: float
  charge: float
  type: str            # AutoDock atom type (C, OA, N, SA, ...)
```

**The mode table has two layouts, and both must be parsed.** With CNN scoring on
(the default, and what the fuzzer runs) the columns are
`affinity | intramol | CNN pose score | CNN affinity`. With `--cnn_scoring=none`
it degrades to vina's `affinity | rmsd l.b. | rmsd u.b.`. The presence of a fifth
numeric field distinguishes them. Under the default layout there are **no RMSD
columns at all**, so `rmsd_lb`/`rmsd_ub` stay 0.0 and pose spread must be
computed geometrically from `parse_all_poses` instead.

Parsing only the second column would discard the strain signal and both CNN
columns — that is, everything gnina was chosen for.

`parse_pose` returns atoms from the best pose only and heavy atoms only;
hydrogens are dropped for coverage fingerprinting.

---

## Adding a New Backend

To add a new docking engine:
1. Create a class implementing the `DockingBackend` interface in `biofuzz/docker/`
2. Register it in the `backends` mapping in `get_backend()`
   (`biofuzz/docker/__init__.py`)
3. Set `docking.engine: <name>` in config.yaml

No other module changes are needed. The campaign resolves the engine by name
through `get_backend()` on both the serial and worker-pool paths, and imports
only the `DockingBackend` contract.

A new backend must either populate `DockingMode.cnn_pose_score` and
`cnn_affinity` or leave them `None`. Left `None`, the scoring policy falls back to
the empirical score and the pose-confidence tier skips — degraded, but correct.

---

## Verification

`tests/test_docker.py` exercises this module against the real engine rather than
a mock, including a live dock of indinavir into `hiv_protease` and a check that
`--cpu 1` is passed only when multiple workers are configured. That is most of
the suite's runtime, and it is where drift in gnina's output format would
surface. `tests/test_backend_selection.py` pins the registry contract.

`tests/test_scoring.py` parses a captured gnina log covering both table layouts,
so the parser stays pinned without needing the binary.

Child-process cleanup is tested by injection, because OS signal delivery cannot
be exercised in-process: `test_dock_process_killed_on_keyboard_interrupt` raises
`KeyboardInterrupt` out of `communicate()` and asserts the child is killed, and
the campaign-level test asserts a checkpoint is written and the exception
re-raised. Together they cover the code that runs on a real Ctrl-C.
