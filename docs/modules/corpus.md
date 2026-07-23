# Module: Corpus

**AFL++ analog:** `queue/` directory + AFLFast power schedule + favored entry tracking

The corpus is the prioritized molecule queue. It decides which molecule gets mutated next and how much mutation budget it receives. Good corpus management is what separates a coverage-guided fuzzer from random sampling — molecules that produce novel coverage and strong affinity get amplified; exhausted molecules are deprioritized but never discarded.

---

## Boundary

**Does:**
- Maintain a prioritized queue of `CorpusEntry` objects
- Deduplicate by canonical SMILES
- Implement the power schedule (mutation budget per entry)
- Track "favored" entries (those that uniquely cover a fingerprint bucket)
- Checkpoint to and load from disk
- Trim the corpus to a configured maximum size

**Does not:**
- Compute coverage novelty (Coverage module)
- Perform mutations (Mutator)
- Decide hit/no-hit (Oracle)

---

## CorpusEntry

```
CorpusEntry:
  smiles: str                  # canonical SMILES (dedup key)
  source_id: str               # seed ID or "mutant_of:<parent_id>"
  mutation_lineage: list[str]  # ordered list of mutation_type strings that produced this entry
  times_selected: int          # how many times popped from queue
  times_fuzzed: int            # how many mutation rounds applied
  best_affinity: float | None  # most negative affinity seen
  novelty_score: int           # strongest novelty class seen: 2=strong, 1=weak, 0=none
  finds: int                   # number of hits produced by mutants of this entry
  favored: bool                # uniquely covers at least one bitmap bucket
  priority: float              # effective queue score = base_priority - crowding
                               #   (derived by Corpus; recomputed on pop)
  base_priority: float | None  # intrinsic worth: seed prior, or evidence once docked
  scaffold: str                # Bemis-Murcko scaffold (crowding key)
  rarity: float                # mean contact rarity of the best pose
  ligand_efficiency: float | None
  calibrated: bool             # True once the molecule ITSELF has been docked
```

`mutation_lineage` is a bounded list (max depth ~10) of the mutation type names used to produce this entry from its ancestor. This allows post-run analysis of which mutation operations are productive without the lineage string growing unbounded.

---

## Priority Formula

Priority splits in two. **Base priority** is an entry's intrinsic worth from its
own evidence. **Effective priority** is what the queue orders on: base, discounted
by how crowded the entry's scaffold is.

```
# base_priority -- depends only on this entry
coverage_signal = novelty_score   (strong=2, weak=1, none=0)
affinity_bonus  = max(0, -affinity - 5.0)    # reward for < -5.0 kcal/mol
rarity_bonus    = rarity * RARITY_WEIGHT     # under-explored contacts
find_bonus      = finds * 5.0                # historically productive entries
reuse_penalty   = times_fuzzed * 1.0         # decay to prevent monopolization
favored_bonus   = 20.0 if favored else 0.0   # AFL++-style favored boost

base_priority = max(0.1,
    coverage_signal * NOVELTY_WEIGHT
    + affinity_bonus * AFFINITY_WEIGHT
    + rarity_bonus
    + find_bonus
    - reuse_penalty
    + favored_bonus
)

# effective priority -- depends on the rest of the corpus
crowding = min(12.0, SCAFFOLD_PENALTY_WEIGHT * log2(n_sharing_scaffold))
priority = max(0.1, base_priority - crowding)
```

`NOVELTY_WEIGHT`, `AFFINITY_WEIGHT` and `SCAFFOLD_PENALTY_WEIGHT` are configurable
(config.yaml). The formula is tunable without changing module code.

**Why the split.** Crowding depends on *other* entries and keeps changing after
an entry is queued, so it cannot be baked into a stored score. Folding it into
`base_priority` and recomputing at pop destroys seed priors: an uncalibrated seed
has no evidence of its own, so recomputation floors it at 0.1 and the entire seed
ordering is lost. Seed priors therefore live in `base_priority`, and only the
crowding term is re-derived.

**Why crowding at all.** Every mutant of a good molecule is itself a good
molecule, so without a diversity term the queue collapses onto one chemical
series — 251 approved drugs went in and one 2-hour campaign explored essentially
one of them, returning ten hits from a single scaffold. Log scaling keeps the
first few analogs of a promising series cheap; the cap demotes a runaway series
without exiling it. See `docs/evaluation_2026-07.md` §B2.

**Reuse penalty was 0.1**, against a novelty weight of 10 — an entry had to be
fuzzed 200 times before it fell behind a fresh sibling of equal novelty. It is
now 1.0.

`pop()` re-derives the crowding term and re-queues if it has grown, so `add()`
stays O(log n). Heap records are versioned so exactly one is live per molecule:
without that, repricing either loses entries from the heap or duplicates them.

---

## Power Schedule

The power schedule determines the mutation budget for a selected entry. It mirrors AFL++'s `perf_score` calculation:

```
power_score = 1.0
            + (coverage_signal * 2.0)
            + affinity_bonus
            + (finds * 6.0)
            - (times_fuzzed * 0.2)

budget_multiplier:
  power_score >= 24 → 3×
  power_score >= 12 → 2×
  power_score >=  6 → 1.5×
  otherwise         → 1×

budget = max(1, base_mutations * budget_multiplier)
```

`base_mutations` comes from config. The power schedule rewards entries that have historically produced novelty and hits, giving them more mutation time on each selection.

---

## Favored Entries

AFL++ tracks "favored" entries — for each coverage bucket, the smallest/best corpus entry that uniquely covers it is marked favored. Non-favored entries still run but less often.

BioFuzz equivalent: for each fingerprint hash bucket that has been hit, track which corpus entry first hit it (the "pioneer"). That entry is favored. When a higher-priority entry covers the same bucket, it inherits the favored flag.

Favored entries receive a large priority bonus. This ensures diverse coverage is maintained even as the corpus grows — the fuzzer cannot over-concentrate on one chemotype.

---

## Deduplication and Merging

SMILES must be canonicalized (via RDKit) before any corpus operation. Two entries with the same canonical SMILES are merged:
- `best_affinity`: take the more negative value
- `novelty_score`: take the maximum
- `finds`: take the maximum (not sum — to avoid double-counting)
- `times_fuzzed`: take the maximum
- `times_selected`: take the maximum
- `favored`: logical OR

---

## Trimming

When the corpus reaches `max_size`, the lowest-priority entry is evicted. Trimming uses a reverse min-heap maintained in parallel with the main max-heap to achieve O(log n) eviction. A linear scan over all entries on every add is not acceptable at 50,000+ entries.

Favored entries are immune to eviction regardless of priority. This mirrors AFL++'s behavior where favored entries are never discarded.

---

## Checkpoint Format

The corpus checkpoint is a JSON file containing a list of serialized `CorpusEntry` objects and the current `max_size`. On load, entries are re-inserted via `add()` to rebuild the heap from scratch. SMILES are re-canonicalized on load to guard against format drift.

---

## Build Criterion

```python
corpus = Corpus(max_size=100)

# Fill beyond max_size to verify trimming
for i in range(150):
    corpus.add(CorpusEntry(smiles=f"C{i}", priority=float(i), ...))

assert corpus.size() == 100                    # trimmed to max
top = corpus.pop()
assert top.priority == max of remaining        # highest priority comes out first

corpus.save("corpus_test.json")
corpus2 = Corpus()
corpus2.load("corpus_test.json")
assert corpus2.size() == corpus.size()         # round-trip
assert corpus2.pop().smiles == top_smiles      # order preserved
```
