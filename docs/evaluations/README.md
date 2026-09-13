# Evaluation rounds

BioFuzz is developed in dated evaluation rounds. Each round has one job: find out
what the tool actually does, rather than what the code appears to do.

The protocol is deliberately simple, and the ordering is the point:

1. **State the question and the pass criteria before running anything**, and
   commit them. A criterion written after seeing the results is not a criterion.
2. **Run the tool for real** — a full campaign against a bundled target, plus
   direct measurement of anything the design assumes.
3. **Publish the results either way.** A round that finds the tool produced
   nothing usable is a successful round.
4. **Fix the documentation alongside the code.** Where a design document was
   wrong, the document is corrected too, because that is what the next rebuild
   would follow.

Rounds are historical records. They describe the version they evaluated and are
not updated to track later changes, except for inline notes where a later round
revised a specific number. For current behaviour, read
[`../modules/`](../modules/) and the [README](../../README.md).

| Round | Target | Focus |
|---|---|---|
| [2026-07](2026-07.md) | `hiv_protease` | First end-to-end evaluation: scoring, scheduler, coverage, chemistry, operations |

## Why this cadence

The July 2026 round is the argument for it. The suite was at 92 passing tests,
the module boundaries were clean, and the code read well — and the tool was
producing ten chemically impossible molecules from a single scaffold while being
structurally unable to find the chemical class that works on two of its five
targets. None of that was visible from reading the code or from the tests, which
tested the code that existed rather than the behaviour the design called for.

Running it and measuring it against known answers was the only thing that
surfaced any of it.
