from __future__ import annotations

import pytest

from biofuzz.core.corpus import Corpus, CorpusEntry
from biofuzz.core.fuzzer import compute_priority, compute_priority_weighted


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
