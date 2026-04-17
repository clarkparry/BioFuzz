## 2026-04-13 - Parallelism abort hardening and pool resilience follow-up

- Added bounded manual-abort pool shutdown in `biofuzz/core/fuzzer.py`: pool join now runs in a daemon helper path with timeout, then force-kills worker processes when join stalls so Ctrl-C shutdown cannot hang indefinitely.
- Kept graceful-abort semantics intact (`stopped_reason=keyboard_interrupt`, checkpoint attempt preserved), but removed the hard dependency on a successful blocking `pool.join()` during manual abort.
- Added worker-pool startup fallback: if `Pool(...)` fails at runtime, BioFuzz logs a warning and continues in single-worker mode instead of failing the run.
- Improved docking parallelism granularity by dispatching pool work with `chunksize=1` (with compatibility fallback for test doubles that do not accept chunksize), reducing load imbalance on heterogeneous docking durations.
- Increased seed docking batch depth from `workers * 2` to `workers * 4` to reduce repeated pool submission overhead while keeping bounded in-memory batches.
- Expanded closed-channel detection for pool teardown/result paths to include additional reset/closed-handle cases.
- Added regression coverage in `tests/test_fuzzer.py` for:
  - non-blocking manual-abort shutdown when `pool.join()` hangs,
  - startup fallback when pool creation fails,
  - single-chunk pool dispatch wiring.

## 2026-04-12 - Ctrl-C spam resilience for multiprocessing campaigns

- Added a SIGINT guard in `biofuzz/core/fuzzer.py` that only promotes the first Ctrl-C to `KeyboardInterrupt`; subsequent Ctrl-C signals are ignored during cleanup.
- Pool workers now initialize with `SIGINT` ignored so manual abort control stays in the parent process; this reduces worker-side interrupt races during heavy parallel campaigns.
- Extended pool-pipe handling so `BrokenPipeError`/`EOFError`/`EPIPE` during submission and result collection are consistently mapped to manual-abort behavior.
- During manual abort, pool shutdown/join `BrokenPipe` cleanup noise is now suppressed (still logged for non-abort/error scenarios).
- Added regression coverage for submission-time pipe failures and updated abort-shutdown expectations.

## 2026-04-12 - Ctrl-C responsiveness fix for deferred signal handling windows

- Refined SIGINT guard semantics so repeated Ctrl-C attempts continue to raise until shutdown suppression is explicitly enabled (instead of dropping all later signals after the first observed one).
- Added an `abort_requested` flag driven by the SIGINT handler and checked in:
  - seed load loop
  - mutation main loop
  - pool timeout polling loops (seed + mutation)
- This addresses cases where Python/native multiprocessing internals delay unwind after first signal; once SIGINT is observed, the next poll/loop boundary now forces a manual abort path.

## 2026-04-12 - Worker BrokenPipe traceback suppression on manual abort

- Added worker initializer logic to suppress `multiprocessing` internal `BrokenPipeError` traceback spam emitted during pool teardown races after manual abort.
- Suppression is scoped to worker processes and only hides tracebacks where the current exception is `BrokenPipeError`; normal worker exceptions continue to print as before.

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
## 2026-04-13 - Hashed fingerprint bitmap coverage rollout

- Implemented the `COVERAGE.md` design as the default coverage mode.
- BioFuzz now uses hashed fingerprint novelty (`strong=2`, `weak=1`, `none=0` by default) as the runtime coverage signal for seed triage, corpus priority, mutation power scheduling, CLI summaries, and the TUI.
- The old union residue metric was removed from the live runtime path after verification showed it was no longer used for scheduling. `CorpusEntry.new_bits` remains only as a compatibility fallback when loading older corpus checkpoints.
- Coverage checkpoints persist the epoch-window bitmap state (`current_map`, `previous_map`, `epoch`, occupancy metadata, stable residue mapping, and novelty counters) while still loading legacy union-only checkpoints for resume compatibility.
- Added a new `coverage:` config section in `config.yaml` with validated defaults:
  - `enabled`
  - `mode`
  - `map_size_kib`
  - `occupancy_rotate_threshold`
  - `novelty_weights`
- Runtime TUI coverage now shows bitmap occupancy, epoch, and cumulative strong/weak/none novelty counts. These values come directly from `CoverageMap` counters, so the display adds effectively no extra hot-path overhead.

## 2026-04-13 - TUI redraw throttling and heartbeat deduplication

- Kept the TUI's one-second heartbeat as the source of timer updates for interactive runs, including long single-worker dock waits.
- Changed normal TUI `update()`, `notice()`, and run-log refreshes to honor `refresh_seconds` instead of forcing a full repaint for every progress event.
- Disabled the fuzzer-side pool-wait heartbeat during TUI-enabled runs so interactive sessions do not pay for two independent timer/redraw loops.
- Regression coverage now protects redraw coalescing, fuzzer-heartbeat disablement for TUI runs, and continued one-second timer advancement in the TUI.

## 2026-04-14 - Release accuracy/speed tuning findings

- Fixed a real oracle-accounting issue in `biofuzz/core/fuzzer.py`: BioFuzz had been evaluating selectivity before confirmation and then returning the pre-confirm verdict to corpus scoring, which meant expensive off-target docks could happen twice and unconfirmed hits could still increase `finds`.
- Deferred selectivity until after confirmation docking when confirmation is enabled. This keeps final hit quality the same, avoids redundant off-target work, and makes corpus power scheduling reflect only confirmed hits.
- Deduplicated exact duplicate seed SMILES before seed docking. This is a pure throughput optimization for release because duplicate seeds do not add new information but previously still consumed full dock/oracle work.
- Reused parsed RDKit mols in `filters.py`, `preparation.py`, and `mutator.py` so the prep/mutation hot path no longer reparses the same SMILES repeatedly just to recompute drug-like filters.
- Tightened coverage contact extraction by skipping receptor hydrogens during residue parsing. This removes some false-positive residue contacts and reduces distance checks without changing the overall coverage model.
- Major remaining finding: coverage is still chain-insensitive. Pocket residues are keyed only by residue number, so multimeric targets can alias contacts like `A:42` and `B:42`. I did not change this in the release pass because it would require a cross-cutting checkpoint/config migration, but it is the main remaining accuracy limitation.
- Secondary limitation to keep in mind: if selectivity docking is unavailable, the oracle still treats the hit as passable and records a `Selectivity skipped` note. That fail-open behavior is useful for robustness, but it means hit quality should be interpreted accordingly when off-target infrastructure is missing.

## 2026-04-14 - Chain-aware coverage and explicit selectivity outcomes

- Coverage now uses chain-qualified residue IDs end-to-end (`A:42`, `B:42`, etc.) via the new `biofuzz/protein/residue_keys.py` helper, so multimeric contacts no longer alias in fingerprints, stable residue-to-bit mappings, or hashed checkpoint metadata.
- Coverage checkpoints were versioned to schema `3`. Older residue-number-only checkpoints still migrate automatically for unambiguous single-chain pockets, but ambiguous multimeric checkpoints are now rejected safely instead of being applied with lossy chain collapse.
- The checked-in target configs now store chain-qualified pocket residue IDs, and `.agent/tools/prepare_target_fixture.py` was updated to emit the same format for newly generated fixtures.
- HIV protease now tracks both monomers explicitly in its pocket definition (`A:*` and `B:*` residues), which restores novelty signal across chain-specific binding modes.
- `OracleConfig` gained `selectivity_policy` with `fail_open` and `fail_closed` modes. The default in `config.yaml` remains `fail_open` to preserve robustness unless the user explicitly wants stricter gating.
- Oracle verdict metadata now includes `selectivity_status` with one of `passed`, `failed`, `skipped_unavailable`, or `not_configured`, and run/CLI summaries now report aggregate counters for those outcomes.
- Regression coverage was added for chain-distinct residue fingerprints/bits, legacy checkpoint migration safety, target-config pocket qualification, fail-closed selectivity behavior, and selectivity summary counters.
