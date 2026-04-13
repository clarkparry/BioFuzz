# Coverage Design (Hashed Fingerprint Bitmap)

## Purpose
Define a fast, fuzzing-oriented coverage signal for BioFuzz that does not saturate as quickly as the current union-of-residues metric.

This design treats each ligand-pocket contact pattern as a fingerprint, hashes it into a fixed-size bitmap, and uses that bitmap to detect novelty.

## Goals
1. Keep coverage updates extremely cheap relative to docking.
2. Preserve useful novelty pressure deep into long campaigns.
3. Bound memory usage with fixed-size maps.
4. Keep behavior configurable, with sensible defaults.
5. Retain a human-readable coverage progress metric.

## Non-Goals
1. Exact counting of all unique fingerprints.
2. Zero-collision novelty tracking.
3. Maximizing statistical purity at the expense of runtime.

## Summary Of Approach
1. Compute residue-contact fingerprint as today (`frozenset[int]`).
2. Convert fingerprint to a compact integer bitmask (exact, per pocket).
3. Hash that bitmask into a bitmap index.
4. Track novelty using a windowed epoch bitmap:
   - `current` map: novelty gate
   - `previous` map: rediscovery damping
5. Rotate maps when `current` occupancy crosses threshold.

## Data Model

### 1) Residue Index Mapping
Build once per target pocket:
1. Sort pocket residue IDs.
2. Assign each residue a stable local bit index `[0..N-1]`.

Example:
- pocket residues: `[8, 23, 25, 26]`
- mapping: `{8:0, 23:1, 25:2, 26:3}`

### 2) Fingerprint Mask
Given a fingerprint set of contacted residue IDs:
1. Start `mask = 0`.
2. For each residue ID in fingerprint, set its mapped bit in `mask`.

Result: one integer representing the exact contact pattern for the target pocket.

### 3) Bitmap State
Maintain two equal-size byte maps:
1. `current_map: bytearray(map_size_bytes)`
2. `previous_map: bytearray(map_size_bytes)`

Also maintain:
1. `current_nonzero_count: int` for occupancy tracking.
2. `epoch: int` incremented on rotation.

## Hashing
Use a fast 64-bit integer mixing step before indexing.

Requirements:
1. Deterministic for reproducibility.
2. Good low-bit distribution.
3. Very low CPU overhead.

Recommended: SplitMix64 finalizer-style mix.

Indexing rule:
1. `idx = mixed_hash & (map_size_bytes - 1)`
2. Require `map_size_bytes` to be a power of two.

## Novelty Classification
Given `idx` from the hashed mask:
1. If `current_map[idx] == 0` and `previous_map[idx] == 0`: `strong_novelty`
2. If `current_map[idx] == 0` and `previous_map[idx] != 0`: `weak_novelty`
3. If `current_map[idx] != 0`: `not_novel`

Then set:
1. If `current_map[idx] == 0`, set to `1` and increment `current_nonzero_count`.

## Saturation Strategy (Recommended)
Use windowed epoch rotation.

Rotation trigger:
1. `current_occupancy = current_nonzero_count / map_size_bytes`
2. Rotate when `current_occupancy >= occupancy_rotate_threshold`

Rotation action:
1. `previous_map <- current_map` (swap buffers)
2. Clear new `current_map` to zeros
3. `current_nonzero_count = 0`
4. `epoch += 1`

Default threshold:
- `occupancy_rotate_threshold = 0.55`

Why this works:
1. Prevents permanent saturation collapse.
2. Preserves speed with fixed memory.
3. Retains short-term memory via `previous_map`.

## Config
Map size must be configurable; default is 256 KiB.

Recommended config keys:
1. `coverage.enabled: true`
2. `coverage.mode: "hashed_fingerprint"`
3. `coverage.map_size_kib: 256`
4. `coverage.occupancy_rotate_threshold: 0.55`
5. `coverage.novelty_weights.strong: 2`
6. `coverage.novelty_weights.weak: 1`
7. `coverage.novelty_weights.none: 0`

Derived value:
1. `map_size_bytes = coverage.map_size_kib * 1024`

Validation:
1. `map_size_bytes >= 1024`
2. `map_size_bytes` is power of two
3. `0.1 <= occupancy_rotate_threshold <= 0.95`

## Integration With Existing BioFuzz Logic

### Use Hashed Novelty For Guidance
Replace `new_bits`-driven scheduling with hashed novelty score:
1. `strong_novelty -> novelty_score = 2`
2. `weak_novelty -> novelty_score = 1`
3. `not_novel -> novelty_score = 0`

Use `novelty_score` in priority and power schedule where `new_bits` currently contributes.

Compatibility note:
1. `new_bits` may still exist as a fallback field when loading older corpus checkpoints, but live runs should treat `novelty_score` as the only active coverage signal.

## Persistence / Checkpoint Format
Persist enough state to resume without losing epoch memory.

Suggested JSON payload:
1. `version`
2. `mode`
3. `map_size_bytes`
4. `occupancy_rotate_threshold`
5. `epoch`
6. `current_nonzero_count`
7. `current_map_b64`
8. `previous_map_b64`
9. `pocket_residue_ids`
10. `residue_to_bit_index`

Notes:
1. Base64 encoding is recommended for compact map serialization in JSON.
2. Validate map size and pocket mapping on load; reject incompatible checkpoints.

## Performance Expectations
Relative cost should be:
1. Docking: dominant (orders of magnitude larger).
2. Fingerprint computation: small.
3. Hash+bitmap update: tiny.

Expected impact:
1. Negligible wall-clock overhead versus current coverage bookkeeping.
2. Better long-run guidance due to reduced early saturation.

## Accuracy Tradeoffs
1. Collisions are expected and acceptable.
2. Smaller maps increase collision rate and early novelty loss.
3. Larger maps improve novelty fidelity at modest memory cost.
4. Rotation trades long-term memory for sustained novelty pressure.

Practical map size guidance:
1. `64 KiB`: fastest/smallest, highest collision pressure.
2. `256 KiB` (default): good baseline for most runs.
3. `1024 KiB`: higher fidelity for very long campaigns.

## Implementation Plan
1. Add a new coverage tracker implementation in `biofuzz/core/coverage.py`.
2. Integrate novelty score into `biofuzz/core/fuzzer.py` priority/power calculations.
3. Extend checkpoint save/load to persist hashed map state.
4. Add config parsing defaults and validation for coverage settings.
5. Add runtime/TUI logging fields: occupancy, epoch, strong/weak novelty counts.

## Testing Plan

### Unit Tests
1. Stable residue-to-bit mapping for the same pocket.
2. Deterministic hash index for same fingerprint.
3. Correct novelty classification for strong/weak/none cases.
4. Correct occupancy tracking and rotation behavior.
5. Checkpoint save/load fidelity for map buffers and epoch.
6. Map size validation (power-of-two and bounds).

### Integration Tests
1. Fuzzer run resumes with preserved hashed coverage state.
2. Priority changes when novelty class changes.
3. Runtime progress surfaces occupancy, epoch, and novelty counters.

### Regression Checks
1. No measurable slowdown in end-to-end throughput beyond noise.
2. Novelty events continue after union coverage reaches 100%.

## Operational Metrics To Log
1. `coverage_bitmap_occupancy`
2. `coverage_epoch`
3. `novelty_strong_count`
4. `novelty_weak_count`
5. `novelty_none_count`

These metrics should appear in the TUI, periodic checkpoint logs, and the final run summary.

## Default Recommendation
Use:
1. `mode = hashed_fingerprint`
2. `map_size_kib = 256`
3. `occupancy_rotate_threshold = 0.55`
4. hashed novelty as the only live scheduling/reporting coverage signal

This gives a speed-first implementation with bounded memory and better long-run guidance than union-only residue coverage.
