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

**The combination map alone is not a usable novelty signal.** A 256 KiB map
holds 262,144 slots, so nearly every distinct pose hashes somewhere unseen and
would score `strong`; novelty pinned at maximum is information-free and flattens
the one term the scheduler needs to tell entries apart. It also gives no partial
credit, since `{A,B,C}` and `{A,B,C,D}` land in unrelated slots, leaving "reached
a residue nothing had reached before" inexpressible. Hence the union map, and
hence `strong` being defined by it.

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

Interaction types are approximated from atom typing, not from full geometry:

- `"A:42:hbond_acceptor"` — an AutoDock acceptor-typed ligand atom (`OA`, `NA`,
  `SA`) in contact with the residue
- `"A:42:hbond_donor"` — another polar heavy atom (N, O, S, not acceptor-typed)
  in contact, treated as a possible donor
- `"A:42:hydrophobic"` — a non-polar ligand heavy atom in contact

**This is a documented approximation, and the reason is upstream.** True donor
and acceptor character needs hydrogen positions and a donor–acceptor angle, but
both `parse_pose` and `parse_receptor_residues` strip hydrogens by design
(heavy atoms only), so neither is recoverable here. Enabling interaction typing
therefore multiplies the coverage space by a signal that is directionally right
and geometrically crude. It is off by default for that reason.

When enabled, each residue occupies 4 fingerprint slots (bare contact plus three
types). When disabled, each contributes 1.

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
256 KiB map needs on the order of 144,000 distinct binding modes, which a
CPU-bound campaign will not reach; occupancy stays at 0.000 to three decimals.
This is not a bug. It is why the union map exists, and why `union_coverage()`
rather than `bitmap_occupancy()` is the number to watch.

---

## Reporting

| Metric | Meaning | Typical |
|---|---|---|
| `union_coverage()` | fraction of pocket contacts ever reached | rises quickly at first, then plateaus |
| `bitmap_occupancy()` | fraction of combination hash space used | ~0.000 — sparse by construction |
| `unreached_bits()` | pocket contacts nothing has reached: the frontier | — |

The union denominator is the pocket size, so it varies by target: 48 contact bits
for `hiv_protease` (a dimer-interface site), 19 for `sars_cov2_mpro`.

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
    map_size_bytes: int = 262144,               # 256 KiB, must be a power of two
    occupancy_rotate_threshold: float = 0.55,
    interaction_types_enabled: bool = False,
    novelty_weights: dict[str, int] = {"strong": 2, "weak": 1, "none": 0},
    contact_cutoff: float = 3.5,
)

.observe(pose_atoms, protein_residues=None, smiles=None) -> CoverageObservation
.bitmap_occupancy() -> float        # combination map, drives epoch rotation
.union_coverage() -> float          # the interpretable coverage number
.unreached_bits() -> list[str]      # the frontier
.pioneer_for(slot: int) -> str | None
.stats() -> dict
.save(path: str | Path) -> None
.load(path: str | Path) -> None     # raises CheckpointVersionError on mismatch
```

`observe()` accepts either shape: pose atoms plus receptor residues, which it
routes through `build_fingerprint`, or an already-built fingerprint set, which it
takes as given. `smiles` is optional and only credits pioneers.

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
  new_bits: int                # union bits this pose reached first
  fingerprint_bits: int        # total contact bits in this pose
  union_coverage: float        # campaign-wide, after this pose
  rarity: float                # mean 1/(1 + prior hits) over this pose's bits
```

---

## Checkpoint Format

```json
{
  "version": 4,
  "map_size_bytes": 262144,
  "occupancy_rotate_threshold": 0.55,
  "interaction_types_enabled": false,
  "epoch": 2,
  "current_nonzero_count": 12341,
  "current_map_b64": "<base64>",
  "previous_map_b64": "<base64>",
  "union_map_b64": "<base64>",
  "union_counts": [12, 0, 5, 3],
  "union_nonzero_count": 3,
  "pocket_residue_ids": ["A:25", "A:27", "A:50"],
  "residue_to_bit_index": {"A:25": 0, "A:27": 1, "A:50": 2},
  "novelty_counts": {"strong": 1243, "weak": 892, "none": 5821},
  "pioneers": {"1024": "c1ccc(O)cc1", "2048": "CCO"},
  "bit_pioneers": {"0": "c1ccc(O)cc1"}
}
```

Version mismatch raises `CheckpointVersionError`. The checkpoint is rejected, not
upgraded: the same field names carry different semantics between versions, so
loading one anyway produces a campaign that looks resumed but classifies novelty
against a map it does not understand.

The hash function is part of the stable interface. Changing it invalidates every
existing checkpoint.

---

## Verification

`tests/test_coverage.py` covers this module: novelty classification across all
three classes, union-bit accounting, rarity measured before a pose's own
contribution, hit-frequency bucketing on repeat observations, epoch rotation, and
checkpoint round-trip plus version rejection.

One test is a full-pipeline integration:
`test_observe_with_real_pose_and_receptor_contacts` chains a real
`GninaBackend.dock()` → `parse_pose()` → `parse_receptor_residues()` →
`CoverageMap.observe()` against the `hiv_protease` fixtures, confirming contact
detection fires on a genuinely docked pose rather than only on synthetic
fingerprints. It is slow for that reason, and worth it.
