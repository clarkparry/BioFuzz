from __future__ import annotations

import pytest

from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.fuzzer import (
    compute_mutation_budget,
    compute_power_score,
    compute_priority,
    compute_priority_weighted,
)


def _entry(smiles: str, priority: float) -> CorpusEntry:
    return CorpusEntry(smiles=smiles, source_id="test", priority=priority)


def test_corpus_pop_returns_highest_priority() -> None:
    corpus = Corpus()
    corpus.add(_entry("CCO", 1.0))
    corpus.add(_entry("c1ccccc1", 5.0))
    corpus.add(_entry("CCN", 2.5))

    first = corpus.pop()
    assert first.smiles == "c1ccccc1"


def test_corpus_save_load_preserves_priority_order(tmp_path) -> None:
    corpus = Corpus(max_size=10)
    corpus.add(_entry("A", 1.0))
    corpus.add(_entry("B", 7.0))
    corpus.add(_entry("C", 3.0))

    save_path = tmp_path / "corpus.json"
    corpus.save(save_path)

    loaded = Corpus()
    loaded.load(save_path)

    assert loaded.max_size == 10
    assert loaded.size() == 3
    assert [loaded.pop().smiles, loaded.pop().smiles, loaded.pop().smiles] == ["B", "C", "A"]


def test_corpus_max_size_drops_lowest_priority() -> None:
    corpus = Corpus(max_size=2)
    corpus.add(_entry("low", 1.0))
    corpus.add(_entry("high", 10.0))
    corpus.add(_entry("mid", 5.0))

    assert corpus.size() == 2
    assert {corpus.pop().smiles, corpus.pop().smiles} == {"high", "mid"}


def test_corpus_load_restores_none_max_size(tmp_path) -> None:
    save_path = tmp_path / "corpus.json"
    save_path.write_text('{"max_size": null, "entries": []}', encoding="utf-8")

    corpus = Corpus(max_size=5)
    corpus.load(save_path)

    assert corpus.max_size is None


def test_compute_priority_matches_spec_formula() -> None:
    assert compute_priority(new_bits=2, affinity=-9.0, times_mutated=3) == pytest.approx(23.7)


def test_compute_priority_weighted_uses_overrides() -> None:
    score = compute_priority_weighted(
        new_bits=3,
        affinity=-8.0,
        times_mutated=4,
        priority_new_bit_weight=5.0,
        priority_affinity_weight=2.0,
        priority_reuse_penalty=0.25,
    )
    # (3 * 5.0) + ((8 - 5) * 2.0) - (4 * 0.25)
    assert score == pytest.approx(20.0)


def test_compute_priority_weighted_uses_novelty_score_when_present() -> None:
    score = compute_priority_weighted(
        new_bits=7,
        novelty_score=2,
        affinity=-8.0,
        times_mutated=0,
        priority_new_bit_weight=10.0,
        priority_affinity_weight=1.0,
        priority_reuse_penalty=0.1,
    )

    assert score == pytest.approx(23.0)


def test_corpus_deduplicates_entries_by_smiles() -> None:
    corpus = Corpus()
    corpus.add(CorpusEntry(smiles="CCO", source_id="seed_1", priority=1.0, new_bits=1))
    corpus.add(CorpusEntry(smiles="CCO", source_id="mutant_of:seed_1", priority=7.5, best_affinity=-9.1))

    assert corpus.size() == 1
    entry = corpus.pop()
    assert entry.smiles == "CCO"
    assert entry.priority == pytest.approx(7.5)
    assert entry.new_bits == 1
    assert entry.best_affinity == pytest.approx(-9.1)


def test_power_schedule_increases_budget_for_interesting_entries() -> None:
    boring = CorpusEntry(smiles="CCO", source_id="seed", priority=1.0)
    interesting = CorpusEntry(
        smiles="CCN",
        source_id="seed",
        priority=15.0,
        best_affinity=-10.5,
        new_bits=3,
        finds=1,
    )

    boring_power, boring_budget = compute_mutation_budget(boring, 20)
    interesting_power, interesting_budget = compute_mutation_budget(interesting, 20)

    assert interesting_power > compute_power_score(boring)
    assert interesting_budget > boring_budget


def test_power_schedule_prefers_hashed_novelty_score_when_available() -> None:
    legacy_only = CorpusEntry(
        smiles="CCO",
        source_id="seed",
        priority=1.0,
        new_bits=5,
    )
    hashed = CorpusEntry(
        smiles="CCN",
        source_id="seed",
        priority=1.0,
        new_bits=5,
        novelty_score=1,
    )

    assert compute_power_score(legacy_only) > compute_power_score(hashed)
