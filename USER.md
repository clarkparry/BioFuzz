## 2026-04-14 Patch Summary

- Made coverage chain-aware end-to-end by introducing chain-qualified residue IDs (`A:42`, `B:42`) across receptor parsing, pocket fingerprints, hashed coverage maps, coverage checkpoints, target configs, and the target-fixture generation tool.
- Versioned coverage checkpoints to schema `3` and added safe migration behavior: single-chain legacy residue-number checkpoints still migrate automatically, while ambiguous multimeric legacy checkpoints now fail closed instead of silently aliasing chains.
- Added explicit selectivity outcome tracking with `passed`, `failed`, `skipped_unavailable`, and `not_configured` statuses plus a configurable `oracle.selectivity_policy` (`fail_open` or `fail_closed`), and surfaced aggregate selectivity counters in run/CLI summaries.
- Added regression coverage for chain-distinct fingerprints/coverage bits, target-config qualification, legacy checkpoint migration safety, fail-closed selectivity behavior, and selectivity summary reporting.
