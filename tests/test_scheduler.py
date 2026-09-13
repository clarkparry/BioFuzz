import random

from biofuzz.corpus import Corpus, CorpusEntry
from biofuzz.corpus.scheduler import scaffold_penalty, select_stage


def test_first_selection_is_deterministic_stage():
    entry = CorpusEntry(smiles="CCO", times_selected=1)
    assert select_stage(entry, random.Random(0), has_donor=True) == "deterministic"


def test_havoc_is_reachable_and_dominant_after_first_selection():
    """Havoc must be the common stage once deterministic is done.

    AFL++ spends most of an input's budget in havoc, and it is the primary
    discovery mechanism here too. The trap to avoid is preferring splice
    whenever a donor exists: a donor exists whenever the corpus holds more than
    one molecule, which would make havoc unreachable in practice.
    """
    entry = CorpusEntry(smiles="CCO", times_selected=2)
    rng = random.Random(1234)
    stages = [select_stage(entry, rng, has_donor=True) for _ in range(2000)]

    assert "havoc" in stages
    assert "splice" in stages
    havoc_share = stages.count("havoc") / len(stages)
    assert 0.7 < havoc_share < 0.8


def test_splice_requires_a_donor():
    entry = CorpusEntry(smiles="CCO", times_selected=5)
    rng = random.Random(0)
    stages = {select_stage(entry, rng, has_donor=False) for _ in range(200)}
    assert stages == {"havoc"}


def test_scaffold_penalty_grows_with_crowding():
    assert scaffold_penalty(0) == 0.0
    assert scaffold_penalty(1) == 0.0
    assert scaffold_penalty(2) > 0.0
    assert scaffold_penalty(16) > scaffold_penalty(4) > scaffold_penalty(2)


def test_scaffold_penalty_is_capped():
    """A crowded scaffold is demoted, never exiled."""
    assert scaffold_penalty(10**6) <= 12.0


def test_corpus_tracks_scaffold_counts():
    corpus = Corpus()
    # Three molecules sharing one benzene-ring scaffold.
    for smiles in ("c1ccccc1C", "c1ccccc1CC", "c1ccccc1CCC"):
        corpus.add(smiles, novelty=2, affinity=-8.0)

    scaffolds = {e.scaffold for e in corpus._entries.values()}
    assert len(scaffolds) == 1
    assert corpus.scaffold_count(scaffolds.pop()) == 3
    assert corpus.distinct_scaffolds() == 1


def test_crowded_scaffold_loses_to_fresh_chemistry():
    """Scaffold crowding must outrank an equally-scoring incumbent series.

    Ten analogs of one series enter the corpus, then a molecule with an equal
    coverage/affinity profile but an unseen scaffold. The newcomer has to be
    selected first; otherwise the queue collapses onto a single lineage, since
    every mutant of a good molecule is itself a good molecule.
    """
    corpus = Corpus(novelty_weight=10.0, affinity_weight=1.0)

    for n in range(1, 11):
        corpus.add(f"c1ccccc1{'C' * n}", novelty=2, affinity=-9.0)
    corpus.add("C1CCNCC1", novelty=2, affinity=-9.0)

    assert corpus.pop().smiles == "C1CCNCC1"


def test_pop_reprices_entries_queued_before_their_scaffold_got_crowded():
    """The first analog of a series must not keep its pre-crowding priority."""
    corpus = Corpus(novelty_weight=10.0, affinity_weight=1.0)

    first = corpus.add("c1ccccc1C", novelty=2, affinity=-9.0)
    priority_when_novel = first.priority

    for n in range(2, 12):
        corpus.add(f"c1ccccc1{'C' * n}", novelty=2, affinity=-9.0)

    popped = corpus.pop()
    assert popped.scaffold == first.scaffold
    assert popped.priority < priority_when_novel


def test_eviction_releases_scaffold_count():
    corpus = Corpus(max_size=2)
    corpus.add("c1ccccc1C", novelty=2, affinity=-9.0)
    corpus.add("c1ccccc1CC", novelty=1, affinity=-6.0)
    corpus.add("c1ccccc1CCC", novelty=1, affinity=-6.0)

    assert corpus.size() == 2
    # Counts must track evictions, otherwise the penalty ratchets up forever.
    total = sum(corpus._scaffold_counts.values())
    assert total == corpus.size()


def test_reuse_penalty_advances_the_queue():
    """A repeatedly-fuzzed entry must eventually yield to an untouched sibling.

    The old reuse penalty was 0.1 per fuzz against a novelty weight of 10, so a
    high-novelty entry stayed on top for ~200 iterations.
    """
    corpus = Corpus(novelty_weight=10.0)
    hot = corpus.add("c1ccccc1C", novelty=2, affinity=-9.0)
    fresh = corpus.add("C1CCNCC1", novelty=2, affinity=-9.0)

    for _ in range(5):
        hot.times_fuzzed += 1
    hot.priority = corpus.compute_priority(hot)
    fresh.priority = corpus.compute_priority(fresh)

    assert fresh.priority > hot.priority
