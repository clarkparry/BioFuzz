# Module: Coverage

**AFL++ analog:** `__afl_area_ptr` edge coverage bitmap + novelty classification

The coverage module answers the most important question in the fuzzer: "Has this molecule shown us something new?" It converts a docked pose into a residue-contact fingerprint, hashes that fingerprint into a fixed-size bitmap, and classifies novelty using a two-epoch sliding window. Coverage is the primary feedback signal that drives the entire mutation strategy.

---

## Boundary

**Does:**
- Accept pose atoms + protein residue coordinates → residue contact fingerprint
- Optionally extend fingerprint with per-residue interaction type bits
- Hash fingerprint to a bitmap slot using a deterministic mixing function
- Classify novelty: strong / weak / none (two-epoch sliding window)
- Store log2-bucketed hit frequency per slot (AFL++ byte model)
- Track per-slot pioneer entries for favored-entry marking
- Save/load checkpoint including full bitmap state

**Does not:**
- Parse PDBQT files (Docker/Parser module)
- Store corpus entries (Corpus module)
- Make hit/no-hit decisions (Oracle module)
- Know what molecule produced a fingerprint

---

## Fingerprint Construction

A fingerprint is a frozenset of contacted pocket residue IDs, optionally qualified with interaction type suffixes.

### Step 1: Contact Detection

For each pocket residue, check whether any of its heavy atoms are within `contact_cutoff` Å (default 3.5 Å) of any ligand heavy atom. Hydrogens are excluded from both sides.

Residues are identified by chain-qualified ID: `"A:42"`, `"B:123"`. The full chain-qualified form is mandatory — unqualified residue numbers are ambiguous in multi-chain structures.

### Step 2: Interaction Type Extension (optional, config-controlled)

If `coverage.interaction_types: true` in config, each contacted residue is qualified with an interaction type suffix. This multiplies the effective coverage space by 3–4× with minimal compute overhead.

Interaction types inferred from pose geometry:

- `"A:42:hbond_donor"` — ligand has an H-bond donor (N–H, O–H) within 3.5 Å of a residue H-bond acceptor (O, N), with acceptable donor–acceptor angle
- `"A:42:hbond_acceptor"` — ligand has an H-bond acceptor (O, N) near a residue donor
- `"A:42:hydrophobic"` — non-polar ligand atom (aliphatic/aromatic C, S) within 4.0 Å of a hydrophobic residue atom (Cα, Cβ, aromatic ring)

When interaction typing is enabled, each contact can contribute up to 3 bits to the fingerprint (one per interaction type present). When disabled, each contact contributes 1 bit.

### Step 3: Bitmask

Map the pocket residue IDs to a stable local bit order (lexicographically sorted chain-qualified IDs). Convert the fingerprint frozenset to an integer bitmask. This integer is the canonical fingerprint representation.

### Step 4: Hash to Bitmap Slot

Mix the bitmask with splitmix64 to produce a bitmap slot index in `[0, map_size_bytes)`. The map size must be a power of two (required for the modulo-equivalent `& (size - 1)` masking).

The hash function is part of the module's stable interface. If the implementation changes, all existing checkpoints become invalid and must be discarded.

---

## Novelty Classification

Two bitmaps are maintained: `current` (this epoch) and `previous` (last epoch). Each slot stores one byte.

**Novelty class for an observation:**
- Slot absent from both maps → `strong` (score 2)
- Slot absent from `current` but present in `previous` → `weak` (score 1)
- Slot present in `current` → `none` (score 0)

After classifying:
1. If `strong` or `weak`: mark the slot in `current` and update the byte with log2-bucketed hit frequency
2. Increment the novelty counter for this class
3. If `bitmap_occupancy() ≥ occupancy_rotate_threshold`: rotate epoch (swap maps, clear current)

---

## Hit Frequency Bucketing

AFL++ stores log2-bucketed hit counts in each coverage byte rather than a simple 0/1 presence flag. BioFuzz does the same. This gives the power schedule more signal about which regions of fingerprint space are heavily vs. lightly explored.

| Cumulative hits | Byte value |
|---|---|
| 0 | 0 (empty) |
| 1 | 1 |
| 2 | 2 |
| 3 | 3 |
| 4–7 | 4 |
| 8–15 | 5 |
| 16–31 | 6 |
| 32+ | 7 |

A slot transitioning to a higher bucket (e.g., from 4 to 5) does not count as novelty. Only transitions from empty to non-empty count as novelty.

---

## Epoch Rotation

When `bitmap_occupancy()` (fraction of non-zero slots) reaches `occupancy_rotate_threshold` (default 0.55), the epoch rotates:
- `previous` ← `current`
- `current` ← zeroed bitmap
- `epoch` counter incremented

This prevents long campaigns from saturating the bitmap so that everything becomes `none` novelty. The two-epoch window ensures that patterns seen in the previous epoch produce `weak` novelty (still some signal) rather than no signal at all.

---

## Favored Entry Tracking

The coverage module maintains a `pioneers` mapping: `slot_index → smiles`. When a slot is first hit, the molecule is recorded as the pioneer for that slot. The fuzzer uses pioneers to decide which corpus entries are "favored."

A corpus entry is favored if it is the pioneer of at least one slot. Favored entries receive a large priority boost in the corpus and cannot be evicted by the trimmer.

When a new molecule hits an already-pioneered slot, the pioneer does not change — first hit wins.

---

## Interface

```
CoverageMap(
    pocket_residue_ids: Iterable[str],
    map_size_bytes: int = 262144,               # 256 KiB
    occupancy_rotate_threshold: float = 0.55,
    interaction_types_enabled: bool = False,
    novelty_weights: dict[str, int] = {"strong": 2, "weak": 1, "none": 0},
)

.observe(pose_atoms, protein_residues) -> CoverageObservation
.bitmap_occupancy() -> float
.pioneer_for(slot: int) -> str | None
.save(path: str | Path) -> None
.load(path: str | Path) -> None
.stats() -> dict
```

```
CoverageObservation:
  fingerprint: frozenset[str]
  fingerprint_mask: int
  hash_slot: int
  novelty_class: str           # "strong" | "weak" | "none"
  novelty_score: int           # 2 | 1 | 0
  bitmap_slot_byte: int        # current byte value at hash_slot (0–7)
  bitmap_occupancy: float
  epoch: int
  rotated: bool
```

---

## Checkpoint Format

```json
{
  "version": 3,
  "map_size_bytes": 262144,
  "occupancy_rotate_threshold": 0.55,
  "interaction_types_enabled": false,
  "epoch": 2,
  "current_nonzero_count": 12341,
  "current_map_b64": "<base64>",
  "previous_map_b64": "<base64>",
  "pocket_residue_ids": ["A:25", "A:27", "A:50"],
  "residue_to_bit_index": {"A:25": 0, "A:27": 1, "A:50": 2},
  "novelty_counts": {"strong": 1243, "weak": 892, "none": 5821},
  "pioneers": {"1024": "c1ccc(O)cc1", "2048": "CCO"}
}
```

Version mismatch → reject checkpoint, do not upgrade silently.

---

## Build Criterion

```python
from biofuzz.coverage import CoverageMap

cov = CoverageMap(
    pocket_residue_ids={"A:25", "A:27", "A:50"},
    map_size_bytes=1024,               # small for testing
    occupancy_rotate_threshold=0.8,
)

fp1 = frozenset({"A:25", "A:27"})
fp2 = frozenset({"A:25", "A:50"})    # different fingerprint
fp3 = frozenset({"A:25", "A:27"})    # same as fp1

obs1 = cov.observe(fp1)
assert obs1.novelty_class == "strong"
assert obs1.novelty_score == 2

obs2 = cov.observe(fp2)
assert obs2.novelty_class == "strong"     # different slot

obs3 = cov.observe(fp3)
assert obs3.novelty_class == "none"       # fp1's slot already in current

# Epoch rotation
cov2 = CoverageMap(pocket_residue_ids={"A:25", "A:27", "A:50"},
                   map_size_bytes=1024, occupancy_rotate_threshold=0.01)
cov2.observe(fp1)                         # triggers rotation
assert cov2.epoch == 1
obs4 = cov2.observe(fp1)
assert obs4.novelty_class == "weak"       # fp1 in previous, not current

# Checkpoint round-trip
import tempfile, os
with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
    path = f.name
cov.save(path)
cov_loaded = CoverageMap(pocket_residue_ids={"A:25", "A:27", "A:50"},
                         map_size_bytes=1024)
cov_loaded.load(path)
assert cov_loaded.epoch == cov.epoch
assert cov_loaded.strong_novelty_count == cov.strong_novelty_count
os.unlink(path)
```
