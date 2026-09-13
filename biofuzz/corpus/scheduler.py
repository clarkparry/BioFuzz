from __future__ import annotations

import math

from biofuzz.corpus.entry import CorpusEntry

# Affinity better than this (kcal/mol) starts earning priority. Roughly the
# boundary between "docks like anything vaguely drug-shaped" and "interesting".
AFFINITY_BASELINE = -5.0

# Per-entry cost of already having been fuzzed. At 1.0 an entry that has been
# fuzzed a few times falls behind a fresh sibling of equal novelty, which is
# what makes the queue advance instead of re-fuzzing one molecule forever.
REUSE_PENALTY_PER_FUZZ = 1.0

# Scale of the scaffold-crowding penalty. Applied as weight * log2(1 + n) where
# n is how many corpus entries already share this Bemis-Murcko scaffold.
SCAFFOLD_PENALTY_WEIGHT = 3.0

# Ceiling on the scaffold penalty so a crowded scaffold is demoted, never
# permanently exiled -- a genuinely strong lineage should still be reachable.
MAX_SCAFFOLD_PENALTY = 12.0

RARITY_WEIGHT = 4.0


def affinity_bonus(entry: CorpusEntry) -> float:
    if entry.best_affinity is None:
        return 0.0
    return max(0.0, -entry.best_affinity - (-AFFINITY_BASELINE))


def scaffold_penalty(scaffold_count: int, weight: float = SCAFFOLD_PENALTY_WEIGHT) -> float:
    """Crowding penalty for a scaffold already well represented in the corpus.

    Without this the queue collapses onto one chemical series: every mutant of a
    good molecule is itself a good molecule, so it re-enters at high priority and
    crowds out every other scaffold. Log scaling means the first few analogs of a
    promising series are barely penalised, while the twentieth is pushed firmly
    behind unexplored chemistry.
    """
    if scaffold_count <= 1:
        return 0.0
    return min(MAX_SCAFFOLD_PENALTY, weight * math.log2(scaffold_count))


def compute_priority(
    entry: CorpusEntry,
    novelty_weight: float = 1.0,
    affinity_weight: float = 1.0,
    rarity_weight: float = RARITY_WEIGHT,
) -> float:
    """An entry's intrinsic worth, ignoring scaffold crowding.

    Crowding is deliberately *not* folded in here: it depends on the rest of the
    corpus and changes as the corpus grows, so it is applied separately by
    Corpus (see effective_priority) against this stable base.
    """
    coverage_signal = entry.novelty_score
    bonus = affinity_bonus(entry)
    find_bonus = entry.finds * 5.0
    reuse = entry.times_fuzzed * REUSE_PENALTY_PER_FUZZ
    favored_bonus = 20.0 if entry.favored else 0.0
    rarity_bonus = entry.rarity * rarity_weight

    priority = (
        coverage_signal * novelty_weight
        + bonus * affinity_weight
        + rarity_bonus
        + find_bonus
        - reuse
        + favored_bonus
    )
    return max(0.1, priority)


def effective_priority(
    base_priority: float,
    scaffold_count: int,
    scaffold_penalty_weight: float = SCAFFOLD_PENALTY_WEIGHT,
) -> float:
    """Queue priority: intrinsic worth discounted by how crowded its series is."""
    return max(
        0.1, base_priority - scaffold_penalty(scaffold_count, scaffold_penalty_weight)
    )


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


def select_stage(entry: CorpusEntry, rng, has_donor: bool) -> str:
    """Pick the mutation stage for this selection of `entry`.

    AFL++ runs the deterministic stage once per input, then spends the rest of
    that input's budget in havoc with splice mixed in. Havoc is the primary
    discovery mechanism, so it must be the common case: gating splice behind a
    probability matters because a donor is available whenever the corpus holds
    more than one molecule, and preferring splice whenever one exists would make
    havoc unreachable.
    """
    if entry.times_selected <= 1:
        return "deterministic"
    if has_donor and rng.random() < 0.25:
        return "splice"
    return "havoc"
