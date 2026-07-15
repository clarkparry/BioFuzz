from __future__ import annotations

from biofuzz.corpus.entry import CorpusEntry


def affinity_bonus(entry: CorpusEntry) -> float:
    if entry.best_affinity is None:
        return 0.0
    return max(0.0, -entry.best_affinity - 5.0)


def compute_priority(
    entry: CorpusEntry, novelty_weight: float = 1.0, affinity_weight: float = 1.0
) -> float:
    coverage_signal = entry.novelty_score
    bonus = affinity_bonus(entry)
    find_bonus = entry.finds * 5.0
    reuse_penalty = entry.times_fuzzed * 0.1
    favored_bonus = 20.0 if entry.favored else 0.0

    priority = (
        coverage_signal * novelty_weight
        + bonus * affinity_weight
        + find_bonus
        - reuse_penalty
        + favored_bonus
    )
    return max(0.1, priority)


def compute_power_score(entry: CorpusEntry) -> float:
    coverage_signal = entry.novelty_score
    bonus = affinity_bonus(entry)
    return (
        1.0
        + coverage_signal * 2.0
        + bonus
        + entry.finds * 6.0
        - entry.times_fuzzed * 0.2
    )


def mutation_budget(entry: CorpusEntry, base_mutations: int) -> int:
    power_score = compute_power_score(entry)
    if power_score >= 24:
        multiplier = 3.0
    elif power_score >= 12:
        multiplier = 2.0
    elif power_score >= 6:
        multiplier = 1.5
    else:
        multiplier = 1.0
    return max(1, int(base_mutations * multiplier))
