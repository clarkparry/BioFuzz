# BioFuzz: Coverage-Guided Molecular Docking Exploration

> *Applying the principles of feedback-directed fuzzing to drug discovery — treating proteins as programs and molecules as inputs.*

---

## Table of Contents

1. [Concept Overview](#concept-overview)
2. [The Fuzzing Analogy](#the-fuzzing-analogy)
3. [Biological Background](#biological-background)
4. [System Architecture](#system-architecture)
5. [Coverage Metric](#coverage-metric)
6. [The Bug Oracle](#the-bug-oracle)
7. [Mutation Engine](#mutation-engine)
8. [Toolchain](#toolchain)
9. [Pipeline Walkthrough](#pipeline-walkthrough)
10. [Configuration Reference](#configuration-reference)
11. [Recommended Starting Targets](#recommended-starting-targets)
12. [Performance Considerations](#performance-considerations)
13. [Further Reading](#further-reading)

---

## Repository Status

The repository is implemented to the layout and phase structure described in `BIOFUZZ_STRUCTURE.md` and is currently being tracked on the `develop` branch.

The validated local verification path in this workspace is:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python .agent/tools/install_vina.py
.venv/bin/python -m pytest tests -v
.venv/bin/python .agent/tools/runtime_audit.py
.venv/bin/python main.py --target hiv_protease --max-iterations 1 --workers 1
```

Notes for this machine:

- Use `python3`; there is no `python` alias in the current workspace.
- `requirements.txt` now uses the current `rdkit` package name and explicitly declares `scipy` plus `gemmi`, so a local Python 3.12 virtualenv can install a working RDKit/Meeko preparation stack.
- `.agent/tools/install_vina.py` downloads the official AutoDock Vina release into `.agent/tools/bin/vina`, and the docking runner/runtime audit now discover repo-local binaries there in addition to normal `PATH` lookups.
- The unit test suite, the Phase 1 preparation check, the runtime audit, the documented reference-ligand docking flow, and a one-iteration CLI run all succeed locally from `.venv` after installing the local Vina binary.
- New run state is checkpointed under `runs/<stamp>_<target>/corpus/state.json` with a mirrored legacy `corpus.json` snapshot kept for resume compatibility.
- BioFuzz still prefers a system `gnina`/`vina`/`quickvina2`/`quickvina-w` when one exists, but no longer depends on a system-wide installation for local verification.

---

## Concept Overview

BioFuzz is a coverage-guided molecular exploration framework modeled directly on the principles of feedback-directed fuzzing (à la AFL/libFuzzer), applied to the domain of computational drug discovery.

The central insight: **protein-ligand docking is structurally isomorphic to program execution with an input.**

- The **protein** is the program. Its binding pocket defines a fixed search space with internal structure (residues, hydrogen bond donors/acceptors, hydrophobic regions).
- The **molecule (ligand)** is the input. Its shape, charge distribution, and flexibility determine how it interacts with the protein's pocket.
- **Docking** is execution. AutoDock Vina/Gnina evaluates the molecule against the protein and produces a score — analogous to running an input through a harness and observing its behavior.
- **Binding affinity** and **residue contact patterns** are the observable outputs — analogous to code coverage and output behavior.

The goal is to use these outputs to drive iterative mutation of the molecular corpus, preferentially evolving molecules that explore new binding modes or achieve strong affinity — rather than randomly enumerating chemical space.

---

## The Fuzzing Analogy

| Software Fuzzing | BioFuzz |
|---|---|
| Program under test | Protein (e.g., viral protease) |
| Input | Molecule (SMILES/PDBQT) |
| Execution | AutoDock Vina / Gnina docking run |
| Code coverage (edge bitmap) | Residue contact fingerprint |
| Crash | High-affinity, selective binder |
| Crash deduplication | Binding mode clustering |
| Mutation (bit flip, splice) | Fragment swap, R-group change, ring mutation |
| Corpus queue | Prioritized molecule library |
| Seed corpus | ZINC20 drug-like subset |
| Havoc stage | Multi-step stochastic chemical mutation |
| Sanitizer / oracle | Off-target selectivity check |
| Coverage map | Pocket contact bitmask (per residue) |

The key difference from existing virtual screening: **feedback**. Traditional docking screens a library passively. BioFuzz uses each docking result to steer the next round of mutations, concentrating exploration on productive chemical regions — exactly as AFL concentrates fuzzing on paths leading to new coverage.

---

## Biological Background

### Proteins and Amino Acids

A protein is a polypeptide chain — a sequence of **amino acids** linked end-to-end. There are 20 canonical amino acids, each with distinct chemical properties: some are hydrophobic (repel water), some are polar (attract water), some carry charge. This chain folds into a stable 3D structure driven by thermodynamics. The resulting shape is the protein's function.

Each amino acid in the chain is referred to as a **residue**. Residues are numbered sequentially from the N-terminus. When a small molecule docks, it contacts a subset of these residues — this contact pattern is the basis of the coverage metric.

### Binding Pockets and Active Sites

Protein folding creates cavities and grooves on the surface. The **active site** is the functionally critical cavity — for an enzyme, it is where the chemical reaction occurs. Small molecules that fit into the active site can block the protein's function by occupying the space the natural substrate would normally use.

Drug discovery reduces, in many cases, to: *find a molecule that fits the active site tightly, selectively, and with drug-like properties.*

### Binding Affinity

When a molecule binds to a protein, the system releases free energy — the bound state is thermodynamically more favorable than the unbound state. This energy difference is the **binding affinity**, expressed in kcal/mol (negative = favorable). The more negative the value, the tighter the binding.

Vina/Gnina compute a *predicted* binding affinity using an empirical scoring function. Ground truth requires experimental measurement (e.g., isothermal titration calorimetry), but computational predictions are accurate enough to guide exploration.

Rough practical thresholds:
- `> −5.0 kcal/mol`: Weak — likely not useful
- `−6.0` to `−8.0`: Moderate — worth examining
- `−8.0` to `−10.0`: Strong — drug-like range
- `< −10.0`: Very strong — high priority

### Selectivity

A molecule that binds tightly to your target protein but also binds tightly to every other protein in the human body is not a drug — it's a toxin. **Selectivity** is the ratio of affinity for the target versus affinity for off-target proteins (particularly human homologs of pathogen proteins). This is the most clinically meaningful property BioFuzz tracks in its oracle.

### Ligands and SMILES

A **ligand** is any small molecule that binds to a protein. In cheminformatics, ligands are represented as molecular graphs: atoms are nodes, bonds are edges. **SMILES** (Simplified Molecular Input Line Entry System) is the standard text encoding of this graph. For example:
- `CCO` → ethanol
- `c1ccccc1` → benzene
- `CC(=O)Oc1ccccc1C(=O)O` → aspirin

SMILES is the lingua franca of the mutation engine: molecules are read, mutated, and written as SMILES strings.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CORPUS MANAGER                           │
│   Priority queue of molecules, sorted by coverage novelty       │
│   Seeds loaded from ZINC20 drug-like subset                     │
└────────────────────────┬────────────────────────────────────────┘
                         │  dequeue highest-priority molecule
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      MUTATION ENGINE                            │
│   RDKit-based chemical graph mutations                          │
│   Fragment swap / R-group enumeration / atom type mutation      │
│   Validity check → discard chemically invalid mutants           │
└────────────────────────┬────────────────────────────────────────┘
                         │  valid SMILES
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PREPARATION PIPELINE                        │
│   RDKit: SMILES → 3D conformer (ETKDG)                          │
│   Meeko: 3D mol → PDBQT (partial charges, rotatable bonds)      │
└────────────────────────┬────────────────────────────────────────┘
                         │  PDBQT ligand file
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                       DOCKING ENGINE                            │
│   Gnina / AutoDock Vina                                         │
│   Protein PDBQT + Ligand PDBQT + Binding box config             │
│   Output: binding affinity (kcal/mol) + docked pose             │
└────────┬───────────────────────────────────────────────────────-┘
         │                              │
         ▼                              ▼
┌─────────────────┐           ┌───────────────────────┐
│  COVERAGE MAP   │           │    BUG ORACLE         │
│                 │           │                       │
│  Parse pose →   │           │  Affinity threshold   │
│  residue        │           │  Off-target dock      │
│  contact bitmap │           │  Selectivity ratio    │
│                 │           │  Strain energy check  │
│  New bits? →    │           │                       │
│  add to corpus  │           │  Hit? → save to       │
│  + reprioritize │           │  findings corpus      │
└─────────────────┘           └───────────────────────┘
```

---

## Coverage Metric

Coverage in BioFuzz is a **residue contact fingerprint** — a bitmask over the residues of the binding pocket.

### Construction

1. Define the active site residues. For a typical small-molecule binding pocket, this is ~15–30 residues. These are identified once, manually, by inspecting the protein structure in PyMOL or ChimeraX.
2. After each docking run, parse the output pose. For every heavy atom of the ligand, compute distances to every residue of the defined pocket.
3. A residue is considered "contacted" if any of its atoms are within **3.5 Å** of any ligand heavy atom.
4. Produce a bitstring: `bit[i] = 1` if residue `i` was contacted, else `0`.
5. XOR against the global coverage map. Any new `1` bits = new coverage.

### Prioritization

Molecules that set new bits in the coverage map are added to the corpus and given higher mutation priority — exactly as AFL prioritizes inputs that discover new edges. Molecules that produce no new coverage are still stored (may be useful for later splicing) but given lower priority.

### Extended Coverage Signals

Beyond simple residue contacts, you can track richer signals:

- **Interaction type bitmap**: Does the molecule form an H-bond donor interaction with residue X? Acceptor interaction? Hydrophobic contact? Electrostatic? Each is a separate bit.
- **Subpocket occupancy**: Divide the binding site into spatial subregions (e.g., S1, S2, S3 pockets of a protease). Track which subpockets are occupied. This is coarser but faster to compute.
- **Binding mode clusters**: Hash the docked pose into a grid of voxel occupancy over the binding site. New hash = new coverage. More expensive but captures geometric novelty that the residue bitmap misses.

---

## The Bug Oracle

The oracle decides whether a molecule is a "crash" — worth saving to the findings corpus.

### Tier 1: Affinity Threshold (Primary)

```
affinity ≤ AFFINITY_THRESHOLD (default: −9.0 kcal/mol)
```

This is the basic crash condition. The threshold should be calibrated per target — set it approximately 1 kcal/mol stronger than the best known reference ligand for that target.

### Tier 2: Selectivity Check (Most Valuable)

Run the same molecule against a human off-target protein (e.g., the closest human homolog of the pathogen protein) using the same docking setup.

```
selectivity_ratio = affinity_target / affinity_off_target
```

A molecule with `affinity_target = −10.0` and `affinity_off_target = −4.0` has a ratio of 2.5 — strong selective preference. This is the most clinically meaningful oracle condition, analogous to a bug that only manifests in the target environment.

Store the top N molecules ranked by selectivity ratio, not just by raw affinity.

### Tier 3: Strain Energy Check

Vina/Gnina report the internal strain energy of the molecule in its docked pose. A molecule scoring −10 kcal/mol but with +6 kcal/mol of internal strain is suspect — the good score may be an artifact of forcing the molecule into a high-energy conformation. Filter or flag molecules where:

```
strain_energy > STRAIN_THRESHOLD (default: 3.5 kcal/mol)
```

### Tier 4: Pose Reproducibility

At low exhaustiveness (fast docking), results have noise. Before committing a molecule to the findings corpus, re-dock at high exhaustiveness (8–16). If the score holds, it's a confirmed hit.

---

## Mutation Engine

All mutation operations work on the molecular graph via RDKit. Every mutant is validated for chemical sanity before preparation. Invalid molecules (bad valence, disconnected graph, failed 3D embedding) are discarded.

### Core Mutations

#### Fragment Replacement (Bioisostere Swap)
The most chemically meaningful mutation. Replace a functional group with a bioisostere — a group with similar size, shape, and electronic properties but different atoms.

Common bioisostere pairs:
| Original | Replacement |
|---|---|
| Carboxylic acid (`−COOH`) | Tetrazole, hydroxamic acid, sulfonamide |
| Phenyl ring | Pyridine, thiophene, furan |
| Amide (`−CONH−`) | Reversed amide, urea, carbamate |
| Ester | Ketone, thioester |

These are medicinal chemistry's equivalent of structured mutations — they maintain the "spirit" of the molecule while exploring chemical space neighbors.

#### R-Group Enumeration
Fix the core scaffold, systematically vary substituents at one or more positions. Analogous to bit-flipping a byte: change one thing, leave the rest.

#### Linker Variation
When the molecule has two pharmacophoric fragments connected by a chain, vary the chain length (`n = 1..6`), rigidity (single bonds vs. double bond vs. ring), and heteroatom composition. This changes the 3D geometry of how the two fragments are positioned relative to each other.

#### Atom Type Mutation
Swap individual atoms: carbon → nitrogen, carbon → oxygen, sulfur → oxygen. Respects valence. Low-level analog of a byte mutation.

#### Ring Opening / Closing
Open a ring to a chain, or cyclize a chain into a ring. Substantial structural change — analogous to a havoc-mode mutation.

#### Splice
Combine fragments from two high-performing molecules in the corpus. Take the scaffold of molecule A and the substituents of molecule B. Analogous to fuzzing's input splicing.

### Validity Constraints

Before any mutant enters the preparation pipeline, enforce:
- **Valence validity**: RDKit `SanitizeMol()` 
- **Molecular weight**: 150–550 Da (Lipinski-derived)
- **Lipophilicity**: logP −1 to 5
- **Rotatable bonds**: ≤ 10 (excessive flexibility impairs docking accuracy)
- **Connected graph**: no disconnected fragments
- **3D embeddability**: RDKit ETKDG must succeed within 3 attempts

---

## Toolchain

### Gnina
**Role**: Primary docking engine.  
**Why**: Fork of AutoDock Vina with a CNN-based scoring function trained on the PDBbind dataset. More accurate than Vina's empirical function. Critically, supports GPU execution — reducing docking time from ~2 minutes to ~5–15 seconds per molecule on a modern GPU.  
**Key flags**:
```bash
gnina \
  --receptor protein_prepared.pdbqt \
  --ligand   ligand.pdbqt \
  --center_x X --center_y Y --center_z Z \
  --size_x 20 --size_y 20 --size_z 20 \
  --exhaustiveness 4 \       # low for fuzzing, 8-16 for confirmation
  --num_modes 3 \            # top 3 poses
  --out docked.pdbqt \
  --score_only               # optional: score without full search
```

**Fallback**: QuickVina-W — CPU-only but ~8x faster than vanilla Vina, comparable accuracy.

---

### RDKit
**Role**: Molecule manipulation, mutation engine, validity checking, 3D conformer generation.  
**Why**: The standard open-source cheminformatics library. Python API. Handles everything from SMILES parsing to pharmacophore matching.  
**Key operations used**:
```python
from rdkit import Chem
from rdkit.Chem import AllChem, RWMol, Descriptors

# Parse and validate
mol = Chem.MolFromSmiles(smiles)
if mol is None:
    discard()

# Sanitize (valence check etc.)
Chem.SanitizeMol(mol)

# Generate 3D conformer
mol_h = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol_h, AllChem.ETKDGv3())
AllChem.MMFFOptimizeMolecule(mol_h)

# Compute properties for validity filter
mw  = Descriptors.MolWt(mol)
logp = Descriptors.MolLogP(mol)
rot  = Descriptors.NumRotatableBonds(mol)
```

---

### Meeko
**Role**: Convert RDKit 3D molecule to PDBQT format for Vina/Gnina.  
**Why**: Scriptable Python API, modern replacement for the legacy MGLTools prepare scripts. Handles partial charge assignment (Gasteiger method) and rotatable bond detection automatically.  
```python
from meeko import MoleculePreparation

preparator = MoleculePreparation()
preparator.prepare(mol_with_3d)
pdbqt_string = preparator.write_pdbqt_string()
```

---

### ZINC20
**Role**: Seed corpus of drug-like molecules.  
**URL**: https://zinc20.docking.org  
**Why**: 1.4 billion commercially available, drug-like molecules. Pre-filtered for Lipinski compliance. Downloadable as SMILES. Use the "in-stock" + "drug-like" + MW 200–400 subset as your initial seeds — this is chemical space that is already known to be synthesizable and bioavailable, giving your mutations a productive starting point.

---

### Protein Data Bank (RCSB PDB)
**Role**: Source of protein structures (the "programs" being fuzzed).  
**URL**: https://rcsb.org  
**Format**: `.pdb` files — text files listing 3D coordinates of every atom.  
**Usage**: Download the target protein, remove water molecules and co-crystallized ligands (optional: save the co-crystallized ligand as a reference), define the binding box from the ligand coordinates, prepare the protein with ADFRsuite (`prepare_receptor`).

---

### PyMOL / UCSF ChimeraX
**Role**: Visualization — not in the automated pipeline, but essential for setup and sanity-checking.  
**Usage**:
- Identify binding pocket residues and binding box coordinates
- Inspect docked poses of high-scoring molecules
- Understand coverage bitmap: which residues are in the pocket

---

### ADFRsuite
**Role**: Protein preparation for docking.  
**Key script**: `prepare_receptor4.py` — removes water, adds hydrogens, assigns charges, outputs protein PDBQT.  
```bash
prepare_receptor4.py -r protein.pdb -o protein_prepared.pdbqt -A hydrogens
```

---

## Pipeline Walkthrough

### Step 0: Setup (one-time per target)

1. Download protein structure from RCSB PDB.
2. Open in PyMOL. Identify the active site. Note the coordinates of the binding pocket center (x, y, z) and estimate the box dimensions.
3. Prepare protein: `prepare_receptor4.py -r protein.pdb -o protein.pdbqt`
4. Define the pocket residues for coverage tracking (all residues within 6Å of the known active site center).
5. Download ZINC20 seed subset (10,000–100,000 SMILES) as the initial corpus.
6. (Optional) Define off-target protein for selectivity oracle — prepare it the same way.

### Step 1: Seed Preparation

```python
for smiles in seed_corpus:
    mol = prepare_molecule(smiles)     # RDKit: parse, sanitize, embed 3D
    if mol is None: continue
    pdbqt = to_pdbqt(mol)             # Meeko conversion
    corpus.add(pdbqt, smiles, priority=1.0)
```

### Step 2: Main Loop

```python
while True:
    # Dequeue
    molecule = corpus.pop_highest_priority()
    
    # Mutate
    mutants = mutation_engine.mutate(molecule, n=20)
    
    for mutant in mutants:
        # Prepare
        pdbqt = prepare_molecule_to_pdbqt(mutant)
        if pdbqt is None: continue
        
        # Dock
        result = gnina.dock(
            receptor=protein_pdbqt,
            ligand=pdbqt,
            center=BINDING_BOX_CENTER,
            size=BINDING_BOX_SIZE,
            exhaustiveness=4
        )
        
        # Coverage
        contacts = compute_residue_contacts(result.pose, pocket_residues)
        new_bits  = contacts & ~global_coverage_map
        if new_bits:
            global_coverage_map |= new_bits
            corpus.add(mutant, priority=coverage_score(new_bits))
        
        # Oracle
        if result.affinity <= AFFINITY_THRESHOLD:
            if selectivity_oracle(mutant):
                findings.save(mutant, result)
```

### Step 3: Triage

For all molecules in `findings`:
1. Re-dock at `exhaustiveness=16` to confirm score.
2. Filter by strain energy.
3. Cluster by binding mode — deduplicate redundant hits.
4. Rank by selectivity ratio.
5. Top candidates proceed to manual inspection in PyMOL, then (experimentally) to synthesis and assay.

---

## Configuration Reference

| Parameter | Default | Description |
|---|---|---|
| `AFFINITY_THRESHOLD` | `-9.0` | kcal/mol cutoff for oracle Tier 1 |
| `STRAIN_THRESHOLD` | `3.5` | Max internal strain energy (kcal/mol) |
| `EXHAUSTIVENESS_FUZZ` | `4` | Vina exhaustiveness during fuzzing |
| `EXHAUSTIVENESS_CONFIRM` | `16` | Vina exhaustiveness for confirming hits |
| `CONTACT_DISTANCE_CUTOFF` | `3.5` | Å cutoff for residue contact assignment |
| `MAX_MOL_WEIGHT` | `550` | Maximum molecular weight (Da) |
| `MAX_LOGP` | `5.0` | Maximum lipophilicity |
| `MAX_ROT_BONDS` | `10` | Maximum rotatable bonds |
| `MUTATIONS_PER_MOLECULE` | `20` | Mutants generated per corpus entry |
| `CORPUS_SIZE_LIMIT` | `50000` | Max molecules in active corpus |

---

## Recommended Starting Targets

These targets are recommended for initial development because they have well-validated binding sites, available crystal structures with co-crystallized ligands (allowing ground-truth verification), and known inhibitors to benchmark against.

| Target | PDB ID | Disease | Known Inhibitor |
|---|---|---|---|
| SARS-CoV-2 Main Protease (Mpro) | 6LU7 | COVID-19 | Nirmatrelvir (Paxlovid) |
| HIV-1 Protease | 1HVR | HIV/AIDS | Indinavir, Saquinavir |
| EGFR Kinase | 1IEP | Non-small cell lung cancer | Erlotinib, Gefitinib |
| *M. tuberculosis* InhA | 1P44 | Tuberculosis | Isoniazid |
| Thrombin | 1PPB | Thrombosis | Dabigatran |

Start with **HIV-1 Protease (1HVR)**. It has one of the most thoroughly characterized binding sites in structural biology, dozens of known inhibitors across a wide affinity range, and it is the canonical example in both the Vina documentation and most docking tutorials.

---

## Performance Considerations

### Throughput Estimates

| Setup | Time/molecule | Molecules/day |
|---|---|---|
| Vina, exhaustiveness=8, 1 CPU core | ~2 min | ~700 |
| QuickVina-W, exhaustiveness=8, 1 core | ~15 sec | ~5,700 |
| Gnina, exhaustiveness=4, 1 GPU | ~8 sec | ~10,800 |
| Gnina, exhaustiveness=4, 4× GPU | ~8 sec | ~43,000 |

Parallelization is embarrassingly simple — docking jobs are independent. Use Python `multiprocessing` or a job queue (Celery, Ray) to saturate available cores/GPUs.

### The Preparation Bottleneck

At scale, PDBQT preparation (RDKit 3D embedding + Meeko) takes 1–5 seconds per molecule. Strategies:

- **Cache prepared PDBQT**: Store the PDBQT alongside the SMILES in the corpus. Only re-prepare after mutation.
- **Pre-prepare the seed corpus**: Generate all PDBQT files before the main loop starts.
- **Fail fast**: Run validity filters (MW, logP, valence) before attempting 3D embedding — cheap filters first.

### Memory

The coverage bitmap is tiny: 30 residues × 1 bit = 30 bits. Trivial. The corpus can hold millions of SMILES strings in RAM. PDBQT files (~5–20 KB each) should be kept on disk and loaded on demand.

---

## Further Reading

### Docking and Scoring
- Trott & Olson (2010). *AutoDock Vina: Improving the speed and accuracy of docking.* Journal of Computational Chemistry. — The original Vina paper; short and readable.
- McNutt et al. (2021). *GNINA 1.0: molecular docking with deep learning.* Journal of Cheminformatics. — Explains the CNN scoring function and GPU acceleration.

### Cheminformatics and RDKit
- Landrum, G. *RDKit: Open-source cheminformatics.* https://www.rdkit.org/docs/GettingStartedInPython.html
- Ramsundar et al. *Deep Learning for the Life Sciences.* O'Reilly (free on GitHub). — Practical chapters on molecular representation and property prediction.

### Biology Background
- Khan Academy MCAT Biology: *Proteins and Enzymes* section. — Free, well-structured introduction to protein structure and enzyme function.
- RCSB PDB Education Resources: https://www.rcsb.org/pages/education — How to read a PDB structure and find active sites.

### Fuzzing Methodology (for the CS side)
- Zalewski, M. *American Fuzzy Lop (AFL) Technical Details.* https://lcamtuf.coredump.cx/afl/technical_details.txt — The original AFL writeup; the coverage-guided fuzzing model this project is based on.
- Manes et al. (2019). *The Art, Science, and Engineering of Fuzzing: A Survey.* IEEE TDSC. — Comprehensive survey of fuzzing techniques.

### Drug Discovery Context
- Lipinski et al. (1997). *Experimental and computational approaches to estimate solubility and permeability.* Advanced Drug Delivery Reviews. — The foundational "Rule of Five" paper defining drug-likeness.
- Shoichet (2004). *Virtual screening of chemical libraries.* Nature. — Overview of computational screening methodology.
