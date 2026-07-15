# Module: Storage

**AFL++ analog:** `queue/`, `crashes/`, and `.cur_input` — persistence for corpus, findings, and hot-path scratch files

Storage handles everything that needs to outlive a single run: findings from the oracle, corpus checkpoints for resume, coverage bitmap checkpoints, the PDBQT preparation cache, and the fuzzer log file. It defines file formats and path conventions; the fuzzer and triage tool both depend on these conventions being stable.

---

## Boundary

**Does:**
- Save and load corpus checkpoints
- Save and load coverage bitmap checkpoints
- Store findings (one directory per hit: pose PDBQT + metadata JSON)
- Maintain an LRU-bounded in-memory PDBQT cache with disk backing
- Write the fuzzer log file
- Define the run directory structure

**Does not:**
- Decide what is a hit (Oracle)
- Compute coverage (Coverage)
- Know anything about molecules or docking

---

## Run Directory Structure

```
runs/<stamp>_<target>/
├── corpus/
│   └── state.json          # Corpus checkpoint (latest)
├── findings/               # One subdirectory per hit
│   ├── 000001_<ts>_<affinity>/
│   │   ├── pose.pdbqt
│   │   └── metadata.json
│   └── ...
├── cache/                  # On-disk PDBQT cache
│   └── <sha1>.pdbqt
├── coverage.json           # Coverage bitmap checkpoint
└── fuzzer.log              # Full log (all messages, always written)
```

`<stamp>` is `YYYY-MM-DD` (date only). If multiple runs happen on the same day for the same target, a counter suffix is appended: `2025-01-15_hiv_protease_2`.

---

## FindingsStore

Each hit is saved to its own subdirectory. The finding ID is a zero-padded sequential integer.

```
FindingID = atomic counter (not a directory scan)

Directory name: <finding_id:06d>_<YYYYMMDDTHHMMSS>_<affinity>
  e.g.: 000003_20250115T142301_-10.50

metadata.json:
{
  "smiles": "...",
  "affinity": -10.50,
  "strain": 1.2,
  "passed_tiers": ["affinity", "strain"],
  "notes": "passed",
  "mutation_stage": "havoc",
  "mutation_type": "bioisostere_replace",
  "parent_smiles": "...",
  "mutation_lineage": ["add_substituent", "bioisostere_replace"],
  "corpus_source_id": "CHEMBL12345",
  "timestamp": "2025-01-15T14:23:01"
}
```

The finding ID counter is maintained in `findings/counter.json` as a single integer. The counter is incremented atomically (read-increment-write with file locking if multiple triage processes could run concurrently, though the fuzzer is always single-process). This avoids the O(n) directory scan of the previous implementation.

---

## Corpus Checkpoint

The corpus checkpoint is a JSON file:
```json
{
  "version": 2,
  "max_size": 50000,
  "entries": [
    {
      "smiles": "...",
      "source_id": "...",
      "mutation_lineage": ["add_substituent"],
      "times_selected": 3,
      "times_fuzzed": 3,
      "best_affinity": -8.2,
      "novelty_score": 2,
      "finds": 0,
      "favored": false,
      "priority": 21.4
    },
    ...
  ]
}
```

On load, entries are re-inserted into the corpus via `add()`. SMILES are re-canonicalized on load. The `priority` field stored in the checkpoint is used only to restore heap order; it will be recalculated on next selection.

---

## Coverage Checkpoint

The coverage checkpoint stores full bitmap state for resume:
```json
{
  "version": 3,
  "map_size_bytes": 262144,
  "occupancy_rotate_threshold": 0.55,
  "epoch": 2,
  "current_nonzero_count": 12341,
  "current_map_b64": "<base64>",
  "previous_map_b64": "<base64>",
  "pocket_residue_ids": ["A:25", "A:27", ...],
  "residue_to_bit_index": {"A:25": 0, "A:27": 1, ...},
  "novelty_counts": {"strong": 1243, "weak": 892, "none": 5821},
  "pioneers": {"1024": "c1ccc(O)cc1", ...}
}
```

If the schema version in the checkpoint does not match the current version, the checkpoint is rejected (not upgraded). The user must start fresh. Silently loading an incompatible checkpoint produces incorrect novelty classifications.

---

## PDBQT Cache

The PDBQT cache prevents re-preparing molecules seen in prior runs or earlier in the current run. It is LRU-bounded in memory with disk backing.

```
PDBQTCache:
  max_memory_entries: int   # default 10,000 (~100MB)
  root_dir: Path

  .get(smiles: str) -> str | None
  .set(smiles: str, pdbqt: str) -> None
  .evict_memory_lru() -> None   # called automatically on overflow
```

Cache key: SHA-256 of the canonical SMILES string (hex digest). This is collision-resistant for practical purposes.

**Cache invalidation:** The cache has no automatic invalidation on config change (e.g., new Meeko version, different MW limits). If preparation parameters change between runs, delete `runs/<stamp>/cache/` before resuming. This is documented in the README, not handled automatically.

---

## Fuzzer Log

The fuzzer log (`runs/<stamp>/fuzzer.log`) receives all messages routed through the logger callback, regardless of whether a TUI is active. Messages are appended with timestamp and level prefix:

```
2025-01-15 14:23:01 [INFO] [SEED] adding 142 seeds to corpus
2025-01-15 14:23:01 [WARN] receptor not found for coverage parsing: ...
2025-01-15 14:23:45 [HIT] c1ccc(O)cc1 | affinity=-10.50 | confirmed_exhaustiveness=16
2025-01-15 14:25:00 [CHKPT] iterations=500 occupancy=0.241 epoch=0 corpus=412
```

The log file is opened on campaign start and closed on campaign end. It is never rotated during a run.

---

## Build Criterion

```python
from biofuzz.storage import FindingsStore, PDBQTCache

# FindingsStore
store = FindingsStore("test_findings/")
path = store.save(
    smiles="c1ccccc1",
    verdict={"affinity": -10.5, "passed_tiers": ["affinity"]},
    pose_path="test_pose.pdbqt",
    affinity=-10.5,
)
assert path.exists()
assert (path / "metadata.json").exists()
assert (path / "pose.pdbqt").exists()

# Counter is atomic (no directory scan)
path2 = store.save(smiles="CCO", verdict={}, pose_path="test_pose.pdbqt", affinity=-9.0)
id1 = int(path.name.split("_")[0])
id2 = int(path2.name.split("_")[0])
assert id2 == id1 + 1   # sequential, no gaps

# PDBQTCache
cache = PDBQTCache("test_cache/", max_memory_entries=3)
cache.set("CCO", "PDBQT content A")
assert cache.get("CCO") == "PDBQT content A"
assert cache.get("CCC") is None   # never set

# LRU eviction
cache.set("CCC", "B")
cache.set("CCCC", "C")
cache.set("CCCCC", "D")   # triggers eviction of "CCO" (oldest)
# memory cache has evicted CCO but disk still has it
result = cache.get("CCO")
assert result == "PDBQT content A"   # retrieved from disk
```
