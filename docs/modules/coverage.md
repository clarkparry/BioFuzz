# Module: Coverage

**AFL++ analog:** `__afl_area_ptr` edge coverage bitmap + novelty classification

The coverage module answers the most important question in the fuzzer: "Has this molecule shown us something new?" It converts a docked pose into a residue-contact fingerprint and classifies novelty. Coverage is the primary feedback signal that drives the entire mutation strategy.

**Two maps, two signals.** This is the part that has to be right:

- **Union map** — one byte per pocket contact bit, indexed by residue (not by
  hash), and **never reset**. Setting a byte that was zero means this pose
  reached a contact no pose in the campaign had reached. This is the direct
  analog of AFL++ discovering a new edge, and it is the signal that decays as the
  pocket gets explored.
- **Combination map** — the whole fingerprint hashed to a single slot, tracking
  distinct *binding modes* rather than distinct contacts, under a two-epoch
  sliding window.

The combination map alone is not a usable novelty signal, and BioFuzz originally
had only that. A 256 KiB map holds 262,144 slots, so nearly every distinct pose
hashes somewhere unseen and scores `strong` — novelty pinned at maximum is
information-free, and it flattened the one term the scheduler needed to tell
entries apart. It also gives no partial credit: `{A,B,C}` and `{A,B,C,D}` land in
unrelated slots, so "reached a residue nothing had reached before" was
inexpressible. See `docs/evaluation_2026-07.md` §C1.

---

## Boundary

**Does:**
- Accept pose atoms + protein residue coordinates → residue contact fingerprint
- Optionally extend fingerprint with per-residue interaction type bits
- Track a never-reset union map of contacts ever reached, and count new bits
- Hash fingerprint to a combination-map slot using a deterministic mixing function
- Classify novelty: strong / weak / none
- Score contact rarity, a signal that does not saturate
- Store log2-bucketed hit frequency per slot (AFL++ byte model)
- Track pioneer entries per slot and per union bit
- Save/load checkpoint including full bitmap state

**Does not:**
- Parse PDBQT files (Docker/Parser module)
- Store corpus entries (Corpus module)
- Make hit/no-hit decisions (Oracle module)
- Know what molecule produced a fingerprint (beyond crediting pioneers)

---

## Fingerprint Construction

A fingerprint is a frozenset of contacted pocket residue IDs, optionally qualified with interaction type suffixes.

> **The pocket residue set is ligand-free.** The residue IDs the fingerprint is
> built over come from `pocket.residue_ids` in the target config, which is
> produced by a pocket detector (P2Rank) run on the apo receptor — not from
> residues near a co-crystallised inhibitor. So the coverage map, like the
> docking box, is a property of the protein and carries no dependency on a known
> binder. Changing the detected residue set changes which contacts are
> observable; it does not change any of the mechanics below.

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

### Step 4a: Update the Union Map

For each fingerprint element, look up its bit in `residue_to_bit_index` and set
that byte in the never-reset `union` map. Count how many were previously zero:
that is `new_bits`, the new-edge signal.

Also accumulate **rarity** — the mean of `1/(1 + prior_hits)` over the pose's
bits, measured *before* this pose's own contribution so a pose is never made to
look less novel by its own contacts. Unlike `new_bits`, rarity never saturates:
once every residue has been touched at least once, `new_bits` is permanently 0
for the rest of the campaign, while rarity keeps distinguishing well-trodden
contacts from lightly explored ones.

### Step 4b: Hash to a Combination Slot

Mix the bitmask with splitmix64 to produce a combination-map slot index in
`[0, map_size_bytes)`. The map size must be a power of two (required for the
modulo-equivalent `& (size - 1)` masking).

The hash function is part of the module's stable interface. If the implementation changes, all existing checkpoints become invalid and must be discarded.

---

## Novelty Classification

Three maps: `union` (never reset), plus `current` (this epoch) and `previous`
(last epoch) for the combination hash. Each slot stores one byte.

**Novelty class for an observation:**
- `new_bits > 0` — reached a contact never reached before → `strong` (score 2)
- Otherwise, combination slot absent from `current` → `weak` (score 1)
- Otherwise → `none` (score 0)

`strong` is defined by the *union* map, not the combination map. That is what
makes it mean "we learned something about this pocket" rather than "this pose
differed from previous poses", and what lets it decay honestly as the pocket
fills.

After classifying:
1. Mark the slot in `current` and update the byte with log2-bucketed hit frequency
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

**Only the combination map rotates.** The union map is the campaign's memory of
which contacts have ever been reached; clearing it would make an already-explored
pocket look novel again and re-trigger `strong` on contacts that are old news.

**Rotation is largely theoretical at CPU docking speeds.** 0.55 occupancy of a
256 KiB map needs ~144,000 distinct binding modes. A real 2-hour campaign reports
`occupancy = 0.000`. This is not a bug — it is why the union map exists, and why
`union_coverage()` rather than `bitmap_occupancy()` is the number to watch.

---

## Reporting

| Metric | Meaning | Typical |
|---|---|---|
| `union_coverage()` | fraction of pocket contacts ever reached | **0.604** (29/48) after 2 iterations on hiv_protease |
| `bitmap_occupancy()` | fraction of combination hash space used | ~0.000 — sparse by construction |
| `unreached_bits()` | pocket contacts nothing has reached: the frontier | — |

`union_coverage` is the interpretable one, and the one surfaced in `RuntimeStatus`
and the checkpoint log. `bitmap_occupancy` only drives epoch rotation.

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
