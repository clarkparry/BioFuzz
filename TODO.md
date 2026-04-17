# TODO: Remaining Accuracy Limitations

_Date: April 14, 2026_

## 1) Major: Coverage Is Still Chain-Insensitive
- `Status`: Open
- `Issue`: Pocket contacts are keyed by residue number only (`int`), so multimeric residues alias (for example, `A:42` and `B:42` are treated as the same residue).
- `Root Cause`: Chain identity is dropped in residue parsing, pocket definition, fingerprint generation, and coverage bit mapping/checkpoint schema.

`Effects on the fuzzer`
- Coverage novelty is undercounted for multimeric targets because chain-distinct contacts collapse to one bit.
- Corpus prioritization loses signal: genuinely new chain-specific binding modes may be scored as already-seen coverage.
- Power scheduling is weakened because novelty-driven energy allocation gets less accurate.
- Long runs can converge earlier than they should, with less exploration diversity in oligomeric interfaces.
- Checkpoint/config migration is required to fix this safely because stored mappings currently assume residue-number-only keys.

`TODO`
- Introduce chain-aware residue keys (for example `"A:42"`) end-to-end.
- Version and migrate coverage checkpoint schema to chain-aware residue mapping.
- Update target pocket config format and fixture generation to emit chain-qualified residue IDs.
- Add regression tests proving `A:42` and `B:42` produce distinct fingerprints and coverage bits.

## 2) Secondary: Selectivity Is Fail-Open When Off-Target Docking Is Unavailable
- `Status`: Open (intentional robustness behavior)
- `Issue`: If selectivity docking/ratio cannot be computed, oracle still permits a hit and records `Selectivity skipped: target/off-target docking unavailable`.
- `Root Cause`: Oracle keeps `selectivity_ok=True` when selectivity is unavailable, rather than treating missing selectivity as a failure.

`Effects on the fuzzer`
- Hit counts and findings can include compounds with unverified off-target behavior.
- `finds` bonuses can promote unverified hits in corpus scoring/power schedule, potentially biasing search toward lower-confidence chemistry.
- Campaign quality metrics can look better than true selectivity quality when off-target infrastructure is missing or unstable.
- Users must interpret hit quality in context of selectivity availability.

`TODO`
- Add explicit selectivity status in verdict metadata (`passed`, `failed`, `skipped_unavailable`, `not_configured`).
- Add configurable policy: `fail_open` (current behavior) vs `fail_closed` (require selectivity when configured).
- Surface summary counters in run/report output for selectivity outcomes, not only overall hit count.
