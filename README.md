# BioFuzz

Coverage-guided fuzzing applied to molecular docking. The protein is the program, the molecule is the input, and docking is execution — mirroring AFL++'s persistent-queue, power-scheduled, staged-mutation design.

Full architecture, module boundaries, and data flow are documented in [`BIOFUZZ_STRUCTURE.md`](BIOFUZZ_STRUCTURE.md). Each module's detailed design lives in [`docs/modules/`](docs/modules/).

A July 2026 in-depth evaluation against a real campaign, and the changes made in
response, are in [`docs/evaluation_2026-07.md`](docs/evaluation_2026-07.md) and
[`docs/improvements_2026-07.md`](docs/improvements_2026-07.md).

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

# 3. Install P2Rank, the ligand-free pocket detector (downloads to .tools/p2rank/).
#    Needs a system Java >= 11 (JAVA_HOME or `java` on PATH); no JRE is bundled.
python .agent/tools/install_p2rank.py

# 4. Build target fixtures — fetches structures from RCSB, prepares the receptor,
#    and derives each target's box + pocket from P2Rank (no reference inhibitor).
python .agent/tools/prepare_target_fixture.py
```

Steps 2–4 are one-time setup: their outputs (`.tools/bin/`, `.tools/p2rank/`, `targets/*/protein.pdbqt`, `targets/*/config.yaml` box/pocket, and the optional `targets/*/reference_ligands/*.pdbqt` validation set) are gitignored and must be rebuilt after every fresh clone. **Java (>= 11) is an external prerequisite** for P2Rank and is not installed by these scripts. To add a target beyond the five bundled ones, see [`docs/adding_targets.md`](docs/adding_targets.md).

## Usage

```sh
# Run a fuzzing campaign against a target (Ctrl-C to stop; checkpoints every 5 min)
./biofuzz-fuzz --target hiv_protease

# Continue an interrupted campaign with its corpus, coverage and findings intact
./biofuzz-fuzz --target hiv_protease --resume runs/<stamp>_hiv_protease

# Triage the findings from a completed run
./biofuzz-triage --findings runs/<stamp>_hiv_protease/findings --target hiv_protease
```

## The oracle is reference-free

BioFuzz is meant to find binders for a protein you have no drug for, so the hit
gate never consults a known inhibitor. There is one reference-free oracle in
`config.yaml`, inherited by every target; no target ships a threshold measured
from where its reference drug docks. The absolute affinity cutoff is coarse by
design — the reference-free quality tiers (ligand efficiency, CNN pose
confidence, vina/CNN consensus) are the real discriminators. See
[`docs/modules/oracle.md`](docs/modules/oracle.md).

The known inhibitors under `targets/<name>/reference_ligands/` are kept only as
an optional *validation* set: dock them by hand to confirm the oracle is not so
strict it would reject a real drug. They never feed the discovery loop.

## Tests

```sh
pytest
```
