# BioFuzz

Coverage-guided fuzzing applied to molecular docking. The protein is the program, the molecule is the input, and docking is execution — mirroring AFL++'s persistent-queue, power-scheduled, staged-mutation design.

Full architecture, module boundaries, and data flow are documented in [`BIOFUZZ_STRUCTURE.md`](BIOFUZZ_STRUCTURE.md). Each module's detailed design lives in [`docs/modules/`](docs/modules/).

## Layout

```
biofuzz/          # Package: corpus, coverage, docker, fuzzer, mutator, oracle, prep, protein, storage, triage, ui
docs/modules/      # Per-module design docs
seeds/             # Seed molecule corpus
targets/<name>/    # Box config per docking target (protein.pdbqt is generated, not committed)
runs/<stamp>/      # Per-campaign checkpoints and findings (gitignored)
config.yaml        # Global defaults
```

## Build

Requires Python 3.12 and a Linux x86_64 host (for the prebuilt GNINA binary).

```sh
# 1. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Install the GNINA docking engine (downloads to .tools/bin/gnina)
python .agent/tools/install_gnina.py

# 3. Build target fixtures — fetches structures from RCSB and prepares the
#    receptor + reference ligand PDBQTs under targets/<name>/
python .agent/tools/prepare_target_fixture.py
```

Steps 2 and 3 are one-time setup: their outputs (`.tools/bin/`, `targets/*/protein.pdbqt`, `targets/*/reference_ligands/*.pdbqt`) are gitignored and must be rebuilt after every fresh clone.

## Usage

```sh
# Run a fuzzing campaign against a target
./biofuzz-fuzz --target hiv_protease

# Triage the findings from a completed run
./biofuzz-triage --findings runs/<stamp>_hiv_protease/findings --target hiv_protease
```

## Tests

```sh
pytest
```
