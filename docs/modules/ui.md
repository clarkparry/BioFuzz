# Module: UI

**AFL++ analog:** The AFL++ status screen — persistent, information-dense, never in the hot path

The UI is completely decoupled from the fuzzer. The fuzzer emits a `RuntimeStatus` dataclass via a callback; the UI receives it and renders whatever it wants. The fuzzer never imports the UI, never knows if a UI is attached, and never waits for a UI operation.

---

## Boundary

**Does:**
- Receive `RuntimeStatus` objects via callback
- Render the current state to some output medium
- Manage its own refresh rate and threading independently
- Expose `log(message)`, `notice(message)`, and `close()` methods

**Does not:**
- Import anything from the fuzzer, corpus, mutator, or oracle
- Block the fuzzer (all rendering is asynchronous)
- Know how molecules are produced or what a docking result looks like
- Store any fuzzer state beyond the last `RuntimeStatus` received

---

## RuntimeStatus Contract

The UI contract is `RuntimeStatus`. The fuzzer emits this; the UI consumes it. Neither side knows how the other is implemented.

```
RuntimeStatus:
  stage: str                    # current fuzzer stage
  mutation_stage: str           # "deterministic" | "splice" | "havoc"
  mutation_type: str | None     # specific operation name
  current_parent: str | None    # SMILES of parent being mutated
  current_smiles: str | None    # SMILES of current candidate
  power_score: float
  mutation_budget: int
  total_docks: int              # dock calls attempted
  completed_docks: int          # dock calls that returned a result
  docks_per_sec: float
  completed_dock_staleness_seconds: float | None
  corpus_size: int
  hits: int
  coverage_bitmap_occupancy: float
  coverage_epoch: int
  novelty_strong: int
  novelty_weak: int
  novelty_none: int
  best_affinity: float | None
  checkpoints: int
  elapsed_seconds: float
  gpu_active: bool | None
```

---

## Default Implementation: Terminal TUI

The default UI is a full-screen terminal display, inspired directly by AFL++'s status screen. It uses the alternate screen buffer (`\x1b[?1049h`) and redraws on each update, coalesced to a configurable refresh rate.

Key display groups (matching AFL++'s layout philosophy):

```
┌─────────────────────────────────────────────────────┐
│ BioFuzz :: <target>                                  │
├── Process Info ──────────────────────────────────────┤
│  Runtime: 01:23:45   Since Last Find: 00:08:12       │
│  Engine: gnina   GPU: active   Workers: 4            │
├── Overall Results ───────────────────────────────────┤
│  Finds: 3   Bitmap Occ: 0.241   Epoch: 0             │
│  Novelty S/W/N: 1243 / 892 / 5821                    │
├── Progress ──────────────────────────────────────────┤
│  Docks Attempted: 8241   Completed: 8238   3.2/sec   │
│  Corpus: 412 entries                                  │
├── Current ───────────────────────────────────────────┤
│  Best Affinity: -11.40 kcal/mol                      │
│  Stage: havoc / bioisostere_replace  Power: 14.2  Budget: 30  │
│  Parent: c1ccc(NC(=O)c2ccccc2)cc1                    │
├── Run Log ───────────────────────────────────────────┤
│  14:23:01  Cc1ccc(C(=O)Nc2ccc(F)cc2)cc1  -11.40     │
│  14:18:44  Cc1ccc(C(=O)Nc2ccccn2)cc1     -10.82     │
│  14:09:33  [SEED][FALLBACK] ...                       │
└─────────────────────────────────────────────────────┘
```

**Refresh throttling:** The TUI redraws at most once per `refresh_seconds` (default 0.25 s). Status updates from the fuzzer that arrive faster than this rate are coalesced — only the most recent status is rendered.

**Heartbeat thread:** A dedicated thread wakes every `heartbeat_seconds` (default 1.0 s) and forces a redraw. This keeps elapsed time and staleness counters ticking even when the fuzzer is blocked in a long dock call and emitting no status updates.

**Log behavior:** `log(message)` appends hits and notable events to a bounded log ring (max 10 entries). The log shows only hits and significant events (fallback messages). All other messages are written to a log file (`runs/<stamp>/fuzzer.log`) regardless of TUI state, so warnings and errors are never invisible.

**Log file:** Every message passed to `log()` or `notice()` is always written to `runs/<stamp>/fuzzer.log`. The TUI display is optional; the log file is not.

**Terminal restoration:** `close()` restores the terminal unconditionally (cursor visible, normal screen). Register an `atexit` handler on construction to ensure restoration even on unexpected crashes.

---

## Alternative Implementations

Since the UI is fully decoupled, alternative implementations require no changes to the fuzzer:

**JSON output mode:** Emit each `RuntimeStatus` as a newline-delimited JSON object to stdout. For scripting, CI pipelines, or remote monitoring.

**Quiet mode:** Print only hits and final summary. For non-interactive contexts.

**No-op mode:** Attach nothing. The fuzzer runs silently.

**Remote dashboard:** Emit status over a websocket. The fuzzer doesn't know the difference.

The active UI implementation is configured in `config.yaml` under `ui.mode: tui | json | quiet`.

---

## Decoupling Guarantee

The fuzzer holds a `ui_callback: Callable[[RuntimeStatus], None] | None` reference. When None, no UI operations occur. When set, the callback is called in the main thread only — never from worker processes. The UI implementation must not block the calling thread for more than a few milliseconds.

---

## Build Criterion

```python
from biofuzz.ui.tui import FuzzerTUI
from biofuzz.fuzzer import RuntimeStatus

tui = FuzzerTUI(target="test", engine="gnina", workers=1, gpu_enabled=False)

status = RuntimeStatus(stage="dock", mutation_stage="havoc", ...)
tui.update(status)   # should not block or raise

tui.log("[HIT] CCO | affinity=-10.5 | ...")
tui.notice("gnina found, GPU inactive")
tui.close()          # terminal must be restored cleanly

# Verify log file was written
import os
assert os.path.exists("runs/test/fuzzer.log")
```
