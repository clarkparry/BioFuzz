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

Sources:
- ChEMBL "approved" drugs, filtered to small molecules (MW ≤ 500)
- DrugBank small molecule approved drugs
- FDA Orange Book with PubChem cross-reference for SMILES

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

> **The MW term must not be an unbounded ramp.** This section previously read
> "higher MW within range → bonus (bigger molecules tend to score better)". The
> parenthetical is true, and that is precisely the problem: docking scores grow
> with heavy-atom count as an *artifact*, so a seed prior that rewards size
> compounds the bias instead of correcting for it. "Within range" was also not
> implemented as a range — the code was `max(0, (mw - 350)/100) * 2`, unbounded,
> so the heaviest seed in the file was always popped first. That is how one 2-hour
> campaign came to spend itself entirely on sildenafil (MW 475). See
> `docs/evaluation_2026-07.md` §B4.
>
> Priority is `base_priority` on the CorpusEntry (intrinsic worth), not
> `priority` (which the Corpus derives by subtracting scaffold crowding).
> Assigning a seed prior to `priority` lets the queue overwrite it.

---

## AFL++ Parallel: Calibration vs. Triage

AFL++ does calibrate seeds on startup (runs them through the target to get their initial coverage bitmap). It does NOT triage/filter seeds — all seeds enter the queue.

BioFuzz mirrors this: when a seed is first popped and docked, that's its calibration. If its affinity is poor, its priority drops and it gets selected less often. It stays in the corpus because it might still be useful as a splice donor. Discarding seeds based on poor initial docking would lose potentially valuable scaffolds.

> **This is `Campaign._calibrate()`, and for a long time it did not exist.** The
> fuzzing loop docked *mutants* only; a corpus entry was never docked itself. So
> BioFuzz never measured whether an approved drug binds the target — the entire
> drug-repurposing question, unasked — and every seed kept `best_affinity = None`
> forever, leaving the power schedule to rank 251 seeds on no evidence at all.
> The behaviour described in this section was specified here and simply never
> implemented. See `docs/evaluation_2026-07.md` §B3.

---

## Seed Set Maintenance

The `seeds/approved_drugs.smi` file is versioned in the repository. When updating:
- Re-run canonicalization (all SMILES must be RDKit canonical form)
- Re-check drug-likeness filters
- Verify scaffold diversity (Murcko scaffold frequency distribution)
- Ensure no duplicates (SMILES dedup by canonical form)

A helper script `scripts/curate_seeds.py` handles canonicalization and deduplication.

---

## Build Criterion

```python
from biofuzz.seeds import load_seeds

seeds = load_seeds("seeds/approved_drugs.smi")
assert len(seeds) >= 50                              # minimum viable set
assert len(seeds) <= 500                             # not too large

from rdkit import Chem
for smiles, seed_id in seeds:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, f"Invalid SMILES in seed: {seed_id}"
    assert smiles == Chem.MolToSmiles(mol, canonical=True), \
        f"Non-canonical SMILES in seed: {seed_id}"

# Verify no duplicates
smiles_set = {smiles for smiles, _ in seeds}
assert len(smiles_set) == len(seeds), "Duplicate SMILES in seed file"
```
