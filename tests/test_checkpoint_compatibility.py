"""Checkpoints must be rejected, never silently reinterpreted.

A corpus or coverage checkpoint written by an incompatible version encodes
different semantics for the same fields. Loading it anyway produces a campaign
that looks resumed but is scheduling on garbage, which is worse than refusing to
start, so both stores reject on a version mismatch.
"""

import json

import pytest

from biofuzz.corpus import Corpus, CorpusEntry
from biofuzz.corpus.corpus import CHECKPOINT_VERSION as CORPUS_VERSION
from biofuzz.coverage import CoverageMap
from biofuzz.coverage.bitmap import CHECKPOINT_VERSION as COVERAGE_VERSION
from biofuzz.storage import CheckpointVersionError


def test_corpus_checkpoint_round_trips(tmp_path):
    corpus = Corpus(max_size=50)
    corpus.add(CorpusEntry(smiles="CCO", source_id="a", base_priority=4.0))
    corpus.add(CorpusEntry(smiles="CCCO", source_id="b", base_priority=9.0))

    path = tmp_path / "state.json"
    corpus.save(path)

    restored = Corpus()
    restored.load(path)
    assert restored.size() == 2
    assert restored.pop().smiles == "CCCO"  # highest priority first


def test_corpus_rejects_a_checkpoint_from_another_version(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": CORPUS_VERSION - 1, "entries": []}))

    with pytest.raises(CheckpointVersionError):
        Corpus().load(path)


def test_corpus_rejects_a_checkpoint_with_no_version(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"entries": []}))

    with pytest.raises(CheckpointVersionError):
        Corpus().load(path)


def test_coverage_rejects_a_checkpoint_from_another_version(tmp_path):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"version": COVERAGE_VERSION - 1}))

    with pytest.raises(CheckpointVersionError):
        CoverageMap(pocket_residue_ids={"A:25"}).load(path)


def test_corpus_load_tolerates_entries_missing_newer_fields(tmp_path):
    """Field additions must not break an otherwise current checkpoint.

    Rejecting on version guards against changed semantics; it should not force a
    restart just because a new optional field was introduced since the file was
    written.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "version": CORPUS_VERSION,
                "max_size": 100,
                "entries": [{"smiles": "CCO", "source_id": "a", "priority": 3.0}],
            }
        )
    )

    corpus = Corpus()
    corpus.load(path)
    assert corpus.size() == 1
