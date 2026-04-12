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
