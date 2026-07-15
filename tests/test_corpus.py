import os
import tempfile

from biofuzz.corpus import Corpus, CorpusEntry


def test_trim_and_pop_highest_priority():
    corpus = Corpus(max_size=100)

    for i in range(150):
        corpus.add(CorpusEntry(smiles="C" * (i + 1), priority=float(i)))

    assert corpus.size() == 100

    remaining_priorities = [e.priority for e in corpus._entries.values()]
    top = corpus.pop()
    assert top.priority == max(remaining_priorities)


def test_checkpoint_roundtrip():
    corpus = Corpus(max_size=100)
    for i in range(10):
        corpus.add(CorpusEntry(smiles="C" * i + "CO", priority=float(i)))

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        corpus.save(path)
        corpus2 = Corpus()
        corpus2.load(path)
        assert corpus2.size() == corpus.size()

        top_smiles_before = max(corpus._entries.values(), key=lambda e: e.priority).smiles
        top_after = corpus2.pop()
        assert top_after.smiles == top_smiles_before
    finally:
        os.unlink(path)


def test_dedup_by_canonical_smiles_merges_stats():
    corpus = Corpus(max_size=100)
    corpus.add("c1ccccc1", novelty=1, affinity=-8.0)
    corpus.add("c1ccccc1", novelty=2, affinity=-9.5)  # same molecule, better stats

    assert corpus.size() == 1
    entry = next(iter(corpus._entries.values()))
    assert entry.novelty_score == 2  # max
    assert entry.best_affinity == -9.5  # more negative wins


def test_favored_entries_immune_to_eviction():
    corpus = Corpus(max_size=5)
    for i in range(4):
        corpus.add(CorpusEntry(smiles="C" * i + "CO", priority=1.0))

    favored_entry = CorpusEntry(smiles="c1ccccc1", priority=0.1, favored=True)
    corpus.add(favored_entry)

    for i in range(4, 10):
        corpus.add(CorpusEntry(smiles="C" * i + "CO", priority=100.0))

    assert corpus.size() <= 5
    assert "c1ccccc1" in corpus._entries  # never evicted despite lowest priority


def test_power_schedule_scales_budget_for_interesting_entries():
    from biofuzz.corpus import compute_power_score, mutation_budget

    strong_entry = CorpusEntry(smiles="CCO", novelty_score=2, best_affinity=-12.0, finds=1)
    weak_entry = CorpusEntry(smiles="CCC", novelty_score=0, best_affinity=None)

    assert compute_power_score(strong_entry) > compute_power_score(weak_entry)
    assert mutation_budget(strong_entry, base_mutations=20) > mutation_budget(
        weak_entry, base_mutations=20
    )


def test_priority_formula_rewards_favored_and_finds():
    from biofuzz.corpus import compute_priority

    base = CorpusEntry(smiles="CCO")
    favored = CorpusEntry(smiles="CCO", favored=True)
    with_finds = CorpusEntry(smiles="CCO", finds=2)

    base_priority = compute_priority(base)
    assert compute_priority(favored) > base_priority
    assert compute_priority(with_finds) > base_priority


def test_mutation_lineage_bounded_depth():
    entry = CorpusEntry(smiles="CCO", mutation_lineage=[f"op{i}" for i in range(15)])
    assert len(entry.mutation_lineage) == 10
