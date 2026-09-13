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

`<stamp>` is `YYYY-MM-DD` (date only). If multiple runs happen on the same day
for the same target, a counter suffix is appended:
`2026-09-11_hiv_protease_2`.

`make_run_dir` always allocates a fresh directory. `open_run_dir`, used by
`--resume`, reattaches to exactly the directory named and never allocates, so a
resumed campaign keeps writing where the interrupted one left off.

Not shown above: `essential.json`, the emergent essential-residue tallies. They
have to survive a restart, because seeds already calibrated are never re-docked
and the signal could not otherwise be rebuilt.

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
  "passed_tiers": ["affinity", "strain", "ligand_efficiency", "cnn_pose_score"],
  "notes": "passed",
  "ligand_efficiency": 0.31,
  "heavy_atom_count": 34,
  "vina_affinity": -10.50,
  "cnn_affinity_kcal": -11.20,
  "cnn_pose_score": 0.84,
  "scoring_policy": "consensus",
  "essential_contacts": 2,
  "novelty_class": "strong",
  "new_coverage_bits": 3,
  "mutation_stage": "havoc",
  "mutation_type": "bioisostere_replace",
  "parent_smiles": "...",
  "mutation_lineage": ["add_substituent", "bioisostere_replace"],
  "corpus_source_id": "approved_042",
  "timestamp": "2026-09-11T14:23:01"
}
```

A finding records **why** it was called a hit — every tier it passed, both
scoring functions separately, and the mutation that produced it — not merely
that it was.

The finding ID counter lives in `findings/counter.json` as a single integer,
incremented under an `fcntl` exclusive lock. This avoids an O(n) directory scan
per save, and the lock makes it safe if a second process ever writes to the same
findings directory.

**Findings are deduplicated by SMILES** (`dedupe=True`, the default). Mutation
readily rediscovers a molecule already found, and saving it twice would
double-count the hit and make triage re-dock it. On construction the store
indexes the SMILES already on disk, so the guarantee survives `--resume`.

---

## Corpus Checkpoint

The corpus checkpoint is a JSON file:
```json
{
  "version": 3,
  "max_size": 50000,
  "entries": [
    {
      "smiles": "...",
      "source_id": "approved_042",
      "mutation_lineage": ["add_substituent"],
      "times_selected": 3,
      "times_fuzzed": 3,
      "best_affinity": -8.2,
      "novelty_score": 2,
      "finds": 0,
      "favored": false,
      "priority": 21.4,
      "base_priority": 24.4,
      "scaffold": "c1ccccc1",
      "rarity": 0.33,
      "ligand_efficiency": 0.29,
      "calibrated": true
    }
  ]
}
```

On load, entries are re-inserted via `add()` to rebuild both heaps, and SMILES
are re-canonicalized. `base_priority` is the durable value; `priority` is
re-derived from it by subtracting current scaffold crowding. `calibrated` is what
tells a resumed campaign which molecules have already been docked themselves, so
it does not pay for them twice.

---

## Coverage Checkpoint

The coverage checkpoint stores full map state for resume; its schema is in
[coverage.md](coverage.md), currently version 4.

If a checkpoint's schema version does not match the current one, **both** stores
raise `CheckpointVersionError` rather than upgrading in place. The same field
names carry different semantics between versions, so loading one anyway yields a
campaign that looks resumed while classifying novelty against a map it does not
understand. Unknown *extra* fields on a corpus entry are dropped rather than
fatal: the version guards changed semantics, not additions.

`Campaign._restore_checkpoints` treats the two differently by design. A missing
or version-mismatched coverage checkpoint is logged and the campaign starts with
a fresh map, since coverage is recoverable signal. A corpus checkpoint that fails
to load is not silently discarded — that would throw away the campaign.

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

Cache key: SHA-256 of the SMILES string, hex digest, used as the on-disk
filename (`<sha256>.pdbqt`).

**Cache invalidation:** there is none. The cache does not know about the Meeko
version or the property bounds in force when an entry was written. If either
changes between runs, delete `runs/<stamp>/cache/` before resuming. This is
documented in the README rather than handled automatically, because the cache
cannot see the inputs that would invalidate it.

---

## Fuzzer Log

The fuzzer log (`runs/<stamp>/fuzzer.log`) receives all messages routed through the logger callback, regardless of whether a TUI is active. Messages are appended with timestamp and level prefix:

```
2026-09-11 15:02:17 [INFO] [ESSENTIAL] derived 4 residues from structure: A:25, B:25, B:8, A:8
2026-09-11 15:02:17 [INFO] [SEED] adding 250 seeds to corpus
2026-09-11 15:04:31 [WARN] [PREP] cannot prepare corpus entry, it will never be docked: ...
2026-09-11 15:06:02 [HIT] c1ccc(O)cc1 | affinity=-10.50 LE=0.31
2026-09-11 15:07:42 [CHKPT] iterations=1 occupancy=0.000 union=0.604 epoch=0 corpus=278 scaffolds=234 hits=0
```

The `[PREP]` warning is logged at WARN precisely because it is easy to miss: a
corpus entry that cannot be prepared is a molecule the campaign will never test,
and the cause is almost always the `molecules:` bounds rather than a defect in
its chemistry.

The log file is opened on campaign start and closed on campaign end. It is never rotated during a run.

---

## Verification

`tests/test_storage.py` covers the run layout, sequential gap-free finding IDs
from the locked counter, metadata round-trip, and the PDBQT cache including
memory eviction with disk fallback.

`tests/test_resume.py` covers reattaching to an existing run directory and the
finding-dedup that stops a resumed campaign re-saving what it already found.
`tests/test_checkpoint_compatibility.py` covers version rejection for both the
corpus and coverage stores.
