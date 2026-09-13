# Module: Seeds

**AFL++ analog:** The seed corpus — trusted, minimal, known-interesting inputs

In AFL++, seeds are inputs that already trigger interesting behavior. The corpus starts with them, and the fuzzer builds from there. AFL doesn't blindly sample a million random byte strings — it starts from real, known-good inputs.

BioFuzz does the same for chemistry: seeds are real, approved small-molecule drugs, not theoretical ZINC compounds. Approved drugs are known to bind proteins in the human body. They have validated bioavailability, synthesis routes, and pharmacology. They are the chemical equivalent of a corpus of known crashing test cases — a biased starting point that immediately occupies meaningful chemical space.

---

## Why Not ZINC?

ZINC contains ~1.4 billion commercially available, theoretically synthesizable, drug-like molecules. The key word is *theoretically*. A random ZINC molecule has a very low baseline probability of binding to any specific target — in typical HTS campaigns, hit rates are 0.01%–0.1%.

Approved drugs, by contrast, have been validated by decades of medicinal chemistry. Even if an approved drug doesn't bind your specific target, it likely binds something structurally similar and its chemical scaffold is known to be bioavailable and manufacturable. Mutations from a good scaffold stay in productive chemical space.

Additionally, a large theoretical seed set requires seed triage (pre-docking every seed to build the initial corpus). With approved drugs, seeds are trusted — they go directly into the corpus without pre-docking. This removes an entire phase of the pipeline and a significant source of complexity.

---

## Seed Corpus Design

### Primary Seed Set: `seeds/approved_drugs.smi`

A curated set of ~100–300 FDA-approved small molecule drugs, selected for:
- Structural diversity (diverse Murcko scaffolds — no 10 statins, no 20 SSRIs)
- Drug-like properties: MW 150–500, logP −1 to 5, oral bioavailability
- Variety of pharmacological targets: kinases, proteases, GPCRs, ion channels, etc.
- No peptides, no biologics, no prodrugs requiring metabolic activation

Source of the bundled file: the 861-compound approved-drug set
(`data/external_library.csv`) published by the
[AiCures / coronavirus_data](https://github.com/yangkevin2/coronavirus_data)
project, canonicalised and filtered down to 250 by `tools/curate_seeds.py`. IDs
are sequential (`approved_001`) because the source data carries no drug names.

That repository declares no license. The entries reproduced here are canonical
SMILES for approved drugs — structural facts rather than authored content — but
regenerate the file from a source whose terms you have checked if you need a
clean provenance chain.

Any comparable approved-drug export works as a replacement — a ChEMBL "approved"
query, a DrugBank small-molecule set, or the FDA Orange Book cross-referenced to
PubChem for SMILES. Feed it to `tools/curate_seeds.py`, which applies the bounds
above.

Format: whitespace-separated SMILES + ID, one molecule per line, no header:
```
CC1=CC=C(C=C1)S(=O)(=O)N  sulfonamide_scaffold
c1ccc2c(c1)cc1ccc3cccc4ccc2c1c34  polycyclic_scaffold
...
```

### No Target-Specific Seeds

BioFuzz deliberately does **not** inject a target's own known inhibitor into the
corpus. The premise of the tool is that you point it at any protein and it finds
a binder; seeding it with the answer for the bundled example targets both
defeats that premise and inflates apparent performance. There is no
`seeds/per_target/` tree.

The only seed source is the target-agnostic `seeds/approved_drugs.smi`. If a
molecule that happens to bind the target is in that curated set, fine — but it
enters on the same drug-likeness prior as every other seed and gets **no**
priority boost for being a known binder. Nothing is added just because it is
known to work on this specific target.

(Known inhibitors still ship under `targets/<name>/reference_ligands/`, but only
as an optional *validation* set — molecules you can dock to check the oracle is
not so strict it rejects real drugs. They never enter the discovery corpus.)

---

## Seed Loading (No Pre-Docking)

Seeds go directly into the corpus at startup. There is no pre-docking phase.

Initial corpus priority for each seed:

```
seed_priority = base_affinity_estimate + scaffold_diversity_bonus

base_affinity_estimate:
  Use drug-likeness metrics as a proxy (no docking needed):
  - MW near the centre of the drug-like band (~350 Da) → modest priority bonus,
    falling off in BOTH directions. Not "higher MW → bonus".
  - Lower logP → modest bonus (better bioavailability)

scaffold_diversity_bonus:
  Assign a small bonus to seeds whose Murcko scaffold is underrepresented
  in the current corpus. This preserves scaffold diversity at startup.
```

This gives the corpus a non-trivial starting priority order without any docking. The first time each seed is popped, it gets docked, its actual affinity is recorded, and its priority is updated to reflect the real coverage signal.

Two constraints on the implementation of that formula are easy to get wrong.

**The MW term must be a band, never an unbounded ramp.** It is true that heavier
molecules tend to score better, and that is precisely the problem: docking scores
grow with heavy-atom count as an *artifact*, so a prior that rewards size
compounds the bias instead of correcting for it. A ramp such as
`max(0, (mw - 350) / 100) * 2` makes the heaviest seed in the file the first one
popped, and the campaign climbs the molecular-weight gradient from there.

**The prior belongs in `base_priority`, not `priority`.** Corpus derives
`priority` from `base_priority` minus scaffold crowding on every insert, so a
prior written to `priority` is overwritten. The diversity term also needs the
corpus's live scaffold census passed in; called without it,
`scaffold_diversity_bonus` returns the same constant for every seed and the
starting order degenerates to file order.

---

## AFL++ Parallel: Calibration vs. Triage

AFL++ does calibrate seeds on startup (runs them through the target to get their initial coverage bitmap). It does NOT triage/filter seeds — all seeds enter the queue.

BioFuzz mirrors this: when a seed is first popped and docked, that's its calibration. If its affinity is poor, its priority drops and it gets selected less often. It stays in the corpus because it might still be useful as a splice donor. Discarding seeds based on poor initial docking would lose potentially valuable scaffolds.

This is `Campaign._calibrate()`, and it is load-bearing rather than an
optimisation. Docking only an entry's *mutants* would mean BioFuzz never measures
whether an approved drug binds the target — the entire repurposing question,
unasked — and every seed would keep `best_affinity = None` forever, leaving the
power schedule to rank all 250 seeds on no evidence at all.

---

## Seed Set Maintenance

The `seeds/approved_drugs.smi` file is versioned in the repository. When updating:
- Re-run canonicalization (all SMILES must be RDKit canonical form)
- Re-check drug-likeness filters
- Verify scaffold diversity (Murcko scaffold frequency distribution)
- Ensure no duplicates (SMILES dedup by canonical form)

A helper script `tools/curate_seeds.py` handles canonicalization and deduplication.

---

## Verification

`tests/test_seeds.py` runs against the real `seeds/approved_drugs.smi`, not a
fixture: every SMILES must parse, be in RDKit canonical form, and be unique, and
the set size must stay within the 50–500 band. `tests/test_seed_loading.py`
covers the priors — that the diversity bonus actually discriminates when given
the corpus census, and that a prior survives insertion.

`tests/test_alerts.py` additionally asserts that none of the 250 seeds trips the
structural-alert catalog, which is what keeps that catalog from being tightened
into something that would reject real drugs.
