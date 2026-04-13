## 2026-04-12 - Ctrl-C broken-pipe hardening for pool submission path

- Hardened manual-abort behavior when multiprocessing pipes break during job submission (`pool.imap_unordered(...)`), not just during iterator result collection.
- Added a shared guard in `biofuzz/core/fuzzer.py` that maps `BrokenPipeError`, `EOFError`, and `OSError(EPIPE)` to `KeyboardInterrupt` with the existing manual-abort warning log path.
- Applied this to both seed-stage batch docking and mutation-stage docking submission/collection loops so abort semantics are consistent in CPU and GPU campaigns.
- Added regression tests for:
  - seed-stage pipe break during pool submission
  - mutation-stage pipe break during pool submission

## 2026-04-12 - Runtime GPU activity signal + parallel seed docking

- Updated GPU status in the TUI to report runtime activity instead of static availability:
  - `active` when a completed GNINA dock did not emit CPU-fallback warnings.
  - `inactive` when GNINA reports `WARNING: No GPU detected...` (or when non-GNINA engines are used).
  - `probing` only before the first conclusive dock result when GPU-capable runtime is expected.
- This uses docking logs already captured by `DockingResult` and sets state once during normal completion accounting, so no extra probe commands are added to the hot path.
- Parallelized seed-stage docking across worker pool batches (same pool strategy used in mutation-stage docking), so `workers` now affects seed throughput as well.
- Added regression tests for:
  - GNINA GPU active/inactive inference in `tests/test_runner.py`.
  - TUI GPU status rendering states in `tests/test_tui.py`.
  - Seed-stage parallelization path in `tests/test_fuzzer.py`.

## 2026-04-12 - GNINA local installer and runtime engine transparency in TUI

- Added `.agent/tools/install_gnina.py` so GNINA can be installed into `.agent/tools/bin/gnina` with a one-command workflow analogous to the existing Vina installer.
- The CLI now resolves the runtime docking binary up front and passes that resolved engine to the TUI, so requesting `gnina` while only `vina` exists now correctly labels the run as `vina`.
- Added a startup runtime notice in the TUI run log for GNINA-specific fallbacks or CPU-only execution (for example: `gnina not installed; using vina` or `gnina found, but GPU is not available`).

## 2026-04-12 - Spammed Ctrl-C hardening during seed docking

- Hardened docking interrupt semantics so child-process SIGINT exits are treated as true manual aborts (`KeyboardInterrupt`) instead of ordinary docking failures.
- SIGINT is now suppressed immediately once a manual abort is captured, preventing repeated `Ctrl-C` from disrupting checkpoint + summary finalization.
- This makes seed-stage abort behavior match regular fuzzing abort behavior under repeated keypresses.

## 2026-04-12 - Seed-stage Ctrl-C behavior aligned with regular fuzzing aborts

- Fixed a control-flow gap where `Ctrl-C` during seed docking could bypass the normal run-status finalization path.
- Seed docking now sets `stopped_reason=keyboard_interrupt` on manual abort and reuses the same shutdown/checkpoint logic as the regular fuzzing loop.
- Removed the seed-stage early return for empty corpus in favor of status-based completion, so summary-producing end-of-run behavior is consistent.
- Added a regression test that injects `KeyboardInterrupt` during seed docking and verifies stop reason + checkpoint persistence.

## 2026-04-12 - Seed docking bootstrap before corpus fuzzing

- Replaced startup behavior that previously inserted every seed directly into the active corpus queue.
- Added a dedicated seed-docking bootstrap phase: each seed is prepared, docked once at fuzz exhaustiveness, scored for coverage/oracle signal, and only retained when interesting.
- Kept resume semantics intact: when a corpus checkpoint exists, seed loading is still skipped and the run resumes from checkpoint state.
- Updated tests to make seed bootstrap deterministic and added coverage that verifies non-interesting seeds are excluded from the initial corpus.

## 2026-04-12 - TUI AFL++ style refresh and redraw behavior

- Reworked the terminal UI into grouped sections (`Process Info`, `Overall Results`, `Progress`, `Findings In Depth`, `State`, `Run Log`) to mirror AFL++ style information clustering.
- Styled group titles in green bold, all metric labels in bold, find count label/value in red, and title in blue as `BioFuzz :: <target>`.
- Changed the event pane from `Recent events` to `Run Log` and restricted entries to hit events only, formatted as `HH:MM:SS  <SMILES>  <affinity>`.
- Switched TUI rendering to alternate-screen redraw with cursor control so repeated updates do not fill terminal scrollback with many historical frames.
- Updated docking progress emissions to refresh on each completed dock (single-worker and multi-worker paths).

## 2026-04-12 - Ctrl-C robustness follow-up

- Reworked pool-result collection to use timeout polling so manual Ctrl-C interrupts are handled promptly while waiting on parallel docking results.
- Removed worker SIGINT ignore and instead catch `KeyboardInterrupt` inside each worker task to avoid noisy traceback spam.
- Hardened pool shutdown so cleanup pipe/join errors are logged as warnings and do not override a manual-abort status.

## 2026-04-12 - Ctrl-C spam handling and status precedence

- Added defensive handling for `BrokenPipeError`/`EPIPE` while collecting pool results; this is now treated as a manual abort path instead of campaign failure when interrupting.
- During manual abort teardown, SIGINT is temporarily ignored to prevent repeated Ctrl-C from interrupting cleanup/checkpoint writes and surfacing noisy pipe exceptions.
- Final checkpoint-save errors after manual abort are now warnings and do not change `stopped_reason` away from `keyboard_interrupt`.

## 2026-04-12 - Seed fallback corpus bootstrap, dock telemetry split, and GNINA runtime fallback

- Added a seed-stage continuity fallback in `biofuzz/core/fuzzer.py`: if fewer than 64 seeds are triaged as "interesting", BioFuzz now retains up to the top 256 successfully docked seeds by affinity so fuzzing can continue from a non-empty corpus.
- Added explicit run-log signaling when this fallback is used via `[SEED][FALLBACK] ...`; the TUI run log now surfaces this entry.
- Split dock accounting into attempted vs completed docks and wired both into progress/status output.
  - `Attempted Docks` increments on every dock call attempt.
  - `Completed Docks` increments only when the docking subprocess completes successfully at the process/runtime layer.
  - `Docks/sec` now reports completed-docks/sec.
- Added TUI health coloring for stalled completion throughput:
  - yellow at 15-29s without a completed dock
  - orange at 30-59s
  - red at >=60s
- Hardened engine resolution in `biofuzz/docking/runner.py` so unrunnable repo-local binaries are skipped. This addresses the observed GNINA runtime failure (`libcudnn.so.9` missing) by automatically falling back to a runnable engine when available.
- Addressed the "missing seeds" gap by retrying seed preparation without strict drug-like gating when strict preparation rejects a seed, while keeping mutation-stage constraints unchanged.

## 2026-04-12 - GNINA dependency install and parser compatibility on non-root host

- Installed CUDA/cuDNN runtime libraries in user space via `.venv` NVIDIA wheels (`cudnn`, `cudart`, `cublas`, `cusolver`, `cusparse`, `cufft`) because root-level apt install was unavailable in this session.
- Replaced `.agent/tools/bin/gnina` with a wrapper script that exports `LD_LIBRARY_PATH` to `.venv` NVIDIA library directories before executing the original binary (`gnina.bin`).
- Verified `gnina --help` and live GNINA docking now run successfully on this host.
- Fixed docking-log parsing compatibility for GNINA's five-column mode table in `biofuzz/docking/parser.py`, and added regression test coverage in `tests/test_parser.py`.

## 2026-04-12 - GNINA runtime preflight generalized and machine-local workaround reverted

- Reverted the machine-specific GNINA launcher wrapper and user-space CUDA/cuDNN injection workaround so behavior no longer depends on local manual path shims.
- Added generalized runtime dependency probing for requested docking engines:
  - `resolve_requested_binary(engine)` resolves the exact requested engine.
  - `runtime_issues_for_binary(path)` detects startup/runtime issues (including missing shared libraries).
- Updated CLI preflight to fail early with actionable dependency details when runtime libs are missing (for example, `gnina runtime (missing shared libraries: libcudnn.so.9)`).
- This dependency check is now portable across users/machines and does not rely on host-specific wrappers.
