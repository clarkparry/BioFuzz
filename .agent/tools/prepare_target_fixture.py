#!/usr/bin/env python3
"""Prepare checked-in BioFuzz target fixtures.

The docking box and pocket residue set are derived from a **ligand-free pocket
detector** (P2Rank) run on the apo receptor -- NOT from a co-crystallised
inhibitor. This is deliberate: BioFuzz is meant to find binders for a protein you
have no drug for, so the active site it searches must be a property of the
protein, not a footprint traced around a molecule you already know binds. An apo
(ligand-free) structure works fine here; a co-crystal is no longer required.

The pocket residue set produced here is exactly what the coverage fingerprint
hashes contacts into (`Campaign._build_coverage` -> `pocket.residue_ids`), so the
novelty map is ligand-free for the same reason.

A known inhibitor, if one is named for a target, is used for ONE optional thing:
emitting a `reference_ligands/` entry that can be docked by hand to check the
oracle is not so strict it would reject a real drug. It never touches the box,
the pocket, or the discovery corpus.

Requires P2Rank on PATH (`prank`) or via $P2RANK. See docs/adding_targets.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGETS_ROOT = REPO_ROOT / "targets"
BUILD_ROOT = REPO_ROOT / ".agent" / "tools" / "target-build"
MK_PREPARE_RECEPTOR = REPO_ROOT / ".venv" / "bin" / "mk_prepare_receptor.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biofuzz.prep.preparation import prepare_smiles
from biofuzz.protein.essential import select_static_essential
from biofuzz.protein.residues import parse_receptor_residues, residue_key

# How many essential (must-engage) residues to record per target. A small set:
# the oracle's essential-contact tier is tolerant (a pose need only touch one),
# so a large set would make it trivial. See biofuzz/protein/essential.py.
ESSENTIAL_COUNT = 4

# Reference-ligand prep only needs a valid embeddable 3D structure, not the
# drug-likeness bounds applied to fuzzed candidates -- bounds are wide open
# so real approved drugs (e.g. indinavir, MW ~614) don't get rejected.
REFERENCE_LIGAND_BOUNDS = dict(
    min_mw=0.0, max_mw=2000.0, max_logp=100.0, max_hbd=100, max_hba=100, max_rot_bonds=100,
)

# Box sizing from the detected pocket's residues: pocket extent + this much
# padding on each axis, clamped to the range below. The clamp keeps a large or
# solvent-exposed pocket from producing an unusably big search box.
BOX_PADDING = 8.0
BOX_MIN = 18.0
BOX_MAX = 28.0


@dataclass(frozen=True)
class TargetSpec:
    name: str
    pdb_id: str
    protein_chains: tuple[str, ...]
    # Optional. When set, P2Rank keeps the predicted pocket that best covers
    # these residues instead of the top-ranked one. These are ACTIVE-SITE
    # residues from literature/UniProt -- knowledge about the protein, not about
    # any inhibitor -- used only to disambiguate which detected pocket is the one
    # you care about (e.g. an allosteric site outranking the catalytic one).
    active_site_residues: tuple[str, ...] = ()
    # Optional. Only used to emit a validation reference ligand; never affects
    # the box or pocket. Leave unset for a target with no known inhibitor.
    ligand_code: str | None = None
    inhibitor_name: str | None = None


TARGET_SPECS: dict[str, TargetSpec] = {
    "egfr_kinase": TargetSpec(
        name="egfr_kinase",
        pdb_id="1M17",
        protein_chains=("A",),
        ligand_code="AQ4",
        inhibitor_name="erlotinib",
    ),
    "parp1": TargetSpec(
        # 2021 redetermination at 2.06 A vs. 4UND's 2015/2.2 A; same compound.
        name="parp1",
        pdb_id="7KK3",
        protein_chains=("A",),
        ligand_code="2YQ",
        inhibitor_name="talazoparib",
    ),
    "sars_cov2_mpro": TargetSpec(
        # 2022 redetermination at 1.50 A vs. 7SI9's 2021/2.0 A; same compound,
        # confirmed wild-type (many newer, higher-res Mpro/nirmatrelvir entries
        # are resistance-mutant studies and were passed over for that reason).
        name="sars_cov2_mpro",
        pdb_id="7VLP",
        protein_chains=("A",),
        ligand_code="4WI",
        inhibitor_name="nirmatrelvir",
    ),
    "braf_v600e": TargetSpec(
        name="braf_v600e",
        pdb_id="3OG7",
        protein_chains=("A",),
        ligand_code="032",
        inhibitor_name="vemurafenib",
    ),
    "hiv_protease": TargetSpec(
        # HIV-1 protease is an obligate homodimer; the active site sits at the
        # A/B interface, so both chains are needed for a complete pocket. This
        # target's config.yaml is hand-curated (flexible flaps) and protected
        # from overwrite by the hand-curated marker -- see docs/adding_targets.md.
        name="hiv_protease",
        pdb_id="1HSG",
        protein_chains=("A", "B"),
        ligand_code="MK1",
        inhibitor_name="indinavir",
    ),
}


def fetch(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "BioFuzz target fixture preparer"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return destination


def parse_ligand_smiles(ligand_cif: Path) -> str:
    text = ligand_cif.read_text(encoding="utf-8")
    patterns = [
        r'^\S+\s+SMILES_CANONICAL\s+CACTVS\s+\S+\s+"([^"]+)"',
        r'^\S+\s+SMILES_CANONICAL\s+"OpenEye OEToolkits"\s+\S+\s+"([^"]+)"',
        r'^\S+\s+SMILES\s+CACTVS\s+\S+\s+"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            return match.group(1)
    raise ValueError(f"Could not find canonical SMILES in {ligand_cif}")


def parse_receptor(
    pdb_path: Path,
    protein_chains: tuple[str, ...],
) -> tuple[list[str], dict[str, list[tuple[float, float, float]]]]:
    """Return the receptor's ATOM lines and its heavy atoms grouped by residue.

    No ligand is read: the box and pocket come from the protein alone.
    """
    receptor_lines: list[str] = []
    residue_atoms: dict[str, list[tuple[float, float, float]]] = {}

    for raw_line in pdb_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.startswith("ATOM  "):
            continue
        altloc = raw_line[16]
        if altloc not in {" ", "A"}:
            continue
        chain_id = raw_line[21].strip()
        if chain_id not in protein_chains:
            continue

        element = raw_line[76:78].strip() or raw_line[12:16].strip()[:1]
        x = float(raw_line[30:38])
        y = float(raw_line[38:46])
        z = float(raw_line[46:54])

        receptor_lines.append(raw_line)
        if element != "H":
            rid = residue_key(chain_id, str(int(raw_line[22:26])))
            residue_atoms.setdefault(rid, []).append((x, y, z))

    if not receptor_lines:
        raise ValueError(f"No receptor atoms found in chains {protein_chains} for {pdb_path}")

    return receptor_lines, residue_atoms


# ---------------- ligand-free pocket detection (P2Rank) ----------------


@dataclass(frozen=True)
class Pocket:
    rank: int
    score: float
    center: tuple[float, float, float]
    residues: list[str]  # residue_key form, e.g. "A:123"


# Repo-local install written by .agent/tools/install_p2rank.py, mirroring how the
# gnina backend falls back to .tools/bin/gnina.
_REPO_LOCAL_P2RANK = REPO_ROOT / ".tools" / "p2rank" / "prank"


def _resolve_p2rank() -> str:
    explicit = os.environ.get("P2RANK")
    if explicit and shutil.which(explicit):
        return shutil.which(explicit)
    for name in ("prank", "p2rank"):
        found = shutil.which(name)
        if found:
            return found
    if _REPO_LOCAL_P2RANK.exists() and os.access(_REPO_LOCAL_P2RANK, os.X_OK):
        return str(_REPO_LOCAL_P2RANK)
    raise RuntimeError(
        "P2Rank not found. The docking box and pocket residues are derived from a "
        "ligand-free pocket detector, not from a co-crystal ligand. Install it with "
        "`python .agent/tools/install_p2rank.py`, or put `prank` on PATH / set $P2RANK."
    )


def _parse_p2rank_residues(field_value: str) -> list[str]:
    """P2Rank writes pocket residues as space-separated `chain_resnum` tokens."""
    residues: list[str] = []
    for token in field_value.split():
        chain, _, resnum = token.partition("_")
        if not resnum:  # unchained token; skip rather than mis-key it
            continue
        residues.append(residue_key(chain, resnum))
    return residues


def detect_pockets(receptor_pdb: Path, workdir: Path) -> list[Pocket]:
    prank = _resolve_p2rank()
    out_dir = workdir / "p2rank"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    subprocess.run(
        [prank, "predict", "-f", str(receptor_pdb), "-o", str(out_dir)],
        check=True,
        cwd=REPO_ROOT,
    )

    predictions = out_dir / f"{receptor_pdb.name}_predictions.csv"
    if not predictions.exists():
        raise RuntimeError(f"P2Rank produced no predictions file at {predictions}")

    pockets: list[Pocket] = []
    with predictions.open() as handle:
        reader = csv.reader(handle, skipinitialspace=True)
        header = [h.strip() for h in next(reader)]
        col = {name: i for i, name in enumerate(header)}
        for row in reader:
            if not row or len(row) < len(header):
                continue
            pockets.append(
                Pocket(
                    rank=int(row[col["rank"]]),
                    score=float(row[col["score"]]),
                    center=(
                        float(row[col["center_x"]]),
                        float(row[col["center_y"]]),
                        float(row[col["center_z"]]),
                    ),
                    residues=_parse_p2rank_residues(row[col["residue_ids"]]),
                )
            )
    if not pockets:
        raise RuntimeError(
            f"P2Rank detected no pockets in {receptor_pdb.name}. Check the structure."
        )
    return sorted(pockets, key=lambda p: p.rank)


def parse_p2rank_residue_scores(residues_csv: Path) -> dict[str, float]:
    """P2Rank's per-residue ligandability, keyed by residue_key.

    Read from ``<receptor>_residues.csv`` (distinct from the pocket-level
    predictions file). Returns an empty map -- so the caller falls back to the
    structure-only ranking -- if the file is absent or lacks the expected columns.
    """
    if not residues_csv.exists():
        return {}
    with residues_csv.open() as handle:
        reader = csv.reader(handle, skipinitialspace=True)
        header = [h.strip() for h in next(reader)]
        col = {name: i for i, name in enumerate(header)}
        chain_i = col.get("chain")
        res_i = col.get("residue_label")
        score_i = next(
            (col[name] for name in ("probability", "zscore", "score") if name in col),
            None,
        )
        if chain_i is None or res_i is None or score_i is None:
            return {}

        scores: dict[str, float] = {}
        for row in reader:
            if len(row) <= max(chain_i, res_i, score_i):
                continue
            chain = row[chain_i].strip()
            resnum = row[res_i].strip()
            if not chain or not resnum:
                continue
            try:
                scores[residue_key(chain, resnum)] = float(row[score_i])
            except ValueError:
                continue
    return scores


def compute_essential_residue_ids(
    receptor_pdbqt: Path,
    pocket_residues: list[str],
    workdir: Path,
    receptor_pdb: Path,
) -> list[str]:
    """The must-engage residues for this pocket, derived from the protein alone.

    Burial + polar character of the pocket residues (from the prepared receptor),
    blended with P2Rank's per-residue ligandability when available. No inhibitor
    is consulted -- this is the same structure-only ranking the campaign falls
    back to at runtime, precomputed here so the checked-in config carries it.
    """
    all_residues = parse_receptor_residues(str(receptor_pdbqt))
    p2rank_scores = parse_p2rank_residue_scores(
        workdir / "p2rank" / f"{receptor_pdb.name}_residues.csv"
    )
    return select_static_essential(
        all_residues,
        set(pocket_residues),
        count=ESSENTIAL_COUNT,
        p2rank_scores=p2rank_scores or None,
    )


def select_pocket(pockets: list[Pocket], active_site_residues: tuple[str, ...]) -> Pocket:
    """Pick which detected pocket to use.

    Default is P2Rank's top-ranked pocket. If active-site residues are given
    (protein knowledge, not inhibitor knowledge), pick the pocket that covers the
    most of them -- this disambiguates targets where the catalytic site is not
    the highest-scoring cavity.
    """
    if active_site_residues:
        hint = set(active_site_residues)
        best = max(pockets, key=lambda p: len(hint.intersection(p.residues)))
        if hint.intersection(best.residues):
            return best
    return pockets[0]


def compute_box(
    pocket: Pocket,
    residue_atoms: dict[str, list[tuple[float, float, float]]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Box center from the pocket centroid; size from its residues' extent.

    The center is P2Rank's pocket center. The size spans the atoms of the
    pocket-lining residues plus padding, clamped -- generous enough not to clip
    the site (the failure mode of a tight ligand-shaped box) without running away
    on an open pocket.
    """
    points = [pt for rid in pocket.residues for pt in residue_atoms.get(rid, [])]
    if not points:
        raise ValueError("Selected pocket has no mappable receptor atoms")

    center = (round(pocket.center[0], 3), round(pocket.center[1], 3), round(pocket.center[2], 3))

    def dimension(values: list[float], c: float) -> float:
        # Symmetric half-extent about the box center, so the center stays put.
        reach = max(abs(max(values) - c), abs(c - min(values)))
        return round(max(BOX_MIN, min(BOX_MAX, 2.0 * reach + BOX_PADDING)), 3)

    size = (
        dimension([p[0] for p in points], center[0]),
        dimension([p[1] for p in points], center[1]),
        dimension([p[2] for p in points], center[2]),
    )
    return center, size


def sorted_residue_ids(residues: list[str]) -> list[str]:
    def key(value: str):
        chain, _, resnum = value.partition(":")
        try:
            return (chain, int(resnum))
        except ValueError:
            return (chain, 0)

    return sorted(set(residues), key=key)


def write_receptor_pdb(receptor_lines: list[str], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join([*receptor_lines, "TER", "END"]) + "\n", encoding="utf-8")
    return destination


def prepare_receptor_pdbqt(receptor_pdb: Path, destination: Path) -> None:
    subprocess.run(
        [
            str(MK_PREPARE_RECEPTOR),
            "--read_pdb",
            str(receptor_pdb),
            "--write_pdbqt",
            str(destination),
            "--allow_bad_res",
            "--default_altloc",
            "A",
        ],
        check=True,
        cwd=REPO_ROOT,
    )


# Set on a config.yaml's first line to protect it from being overwritten by a
# re-run -- e.g. a target whose box/pocket were tuned by hand rather than by the
# pocket detector (see hiv_protease's dimer-interface active site).
HAND_CURATED_MARKER = "# hand-curated"

# Matches trailing per-target override sections (with any preceding comment) so a
# rebuild preserves human-added tuning that this script cannot derive itself --
# principally `molecules:` bounds that admit a target's chemical class.
_OVERRIDE_TAIL_RE = re.compile(
    r"\n(?:#[^\n]*\n)*(?:molecules|oracle|triage|docking):\n.*\Z",
    re.DOTALL,
)


def _preserved_overrides(existing_config: Path) -> str:
    if not existing_config.exists():
        return ""
    match = _OVERRIDE_TAIL_RE.search(existing_config.read_text(encoding="utf-8"))
    return match.group(0) if match else ""


def write_config(
    spec: TargetSpec,
    target_dir: Path,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    residue_ids: list[str],
    essential_residue_ids: list[str],
) -> bool:
    destination = target_dir / "config.yaml"
    if destination.exists() and destination.read_text(encoding="utf-8").startswith(HAND_CURATED_MARKER):
        print(f"Skipping {destination}: hand-curated, not overwriting (see docs/adding_targets.md)")
        return False

    residue_lines = "\n".join(f'    - "{residue_id}"' for residue_id in residue_ids)
    # Must-engage residues, derived from the protein alone (see
    # compute_essential_residue_ids). The oracle's essential-contact tier reads
    # these; a hand-curated config that omits them makes the campaign rederive
    # the same structure-only set at startup.
    essential_lines = "\n".join(f'    - "{residue_id}"' for residue_id in essential_residue_ids)
    essential_block = f"  essential_residue_ids:\n{essential_lines}\n" if essential_residue_ids else ""
    config_text = f'''name: {spec.name}
receptor: protein.pdbqt

box:
  center_x: {center[0]}
  center_y: {center[1]}
  center_z: {center[2]}
  size_x: {size[0]}
  size_y: {size[1]}
  size_z: {size[2]}

pocket:
  contact_cutoff: 3.5
{essential_block}  residue_ids:
{residue_lines}
'''
    config_text += _preserved_overrides(destination)
    destination.write_text(config_text, encoding="utf-8")
    return True


def write_reference_ligand(spec: TargetSpec, target_dir: Path, build_dir: Path) -> None:
    """Optional validation artifact: the known inhibitor, docked by hand to check
    the oracle isn't too strict. Not read by the fuzzer or the box/pocket."""
    if not spec.ligand_code or not spec.inhibitor_name:
        return
    ligand_cif = fetch(
        f"https://files.rcsb.org/ligands/download/{spec.ligand_code}.cif",
        build_dir / f"{spec.ligand_code}.cif",
    )
    smiles = parse_ligand_smiles(ligand_cif)

    reference_dir = target_dir / "reference_ligands"
    reference_dir.mkdir(parents=True, exist_ok=True)
    ligand_slug = spec.inhibitor_name.replace("-", "_")
    (reference_dir / f"{ligand_slug}.smi").write_text(
        f"{smiles} {spec.inhibitor_name}\n",
        encoding="utf-8",
    )
    ligand_pdbqt = prepare_smiles(smiles, **REFERENCE_LIGAND_BOUNDS)
    if ligand_pdbqt is None:
        raise ValueError(f"Failed to prepare reference ligand for {spec.name}: {spec.inhibitor_name}")
    (reference_dir / f"{ligand_slug}.pdbqt").write_text(ligand_pdbqt, encoding="utf-8")


def build_target(spec: TargetSpec) -> None:
    build_dir = BUILD_ROOT / spec.name
    build_dir.mkdir(parents=True, exist_ok=True)

    pdb_path = fetch(f"https://files.rcsb.org/download/{spec.pdb_id}.pdb", build_dir / f"{spec.pdb_id}.pdb")
    receptor_lines, residue_atoms = parse_receptor(pdb_path, spec.protein_chains)

    target_dir = TARGETS_ROOT / spec.name
    target_dir.mkdir(parents=True, exist_ok=True)
    receptor_pdb = write_receptor_pdb(receptor_lines, build_dir / f"{spec.name}_receptor.pdb")
    prepare_receptor_pdbqt(receptor_pdb, target_dir / "protein.pdbqt")

    pockets = detect_pockets(receptor_pdb, build_dir)
    pocket = select_pocket(pockets, spec.active_site_residues)
    residue_ids = sorted_residue_ids(pocket.residues)
    center, size = compute_box(pocket, residue_atoms)
    essential_ids = compute_essential_residue_ids(
        target_dir / "protein.pdbqt", pocket.residues, build_dir, receptor_pdb
    )

    config_written = write_config(spec, target_dir, center, size, residue_ids, essential_ids)
    write_reference_ligand(spec, target_dir, build_dir)

    config_note = "" if config_written else " (config.yaml untouched)"
    print(
        f"prepared {spec.name}: pdb={spec.pdb_id} chains={spec.protein_chains} "
        f"pocket=rank{pocket.rank}/score{pocket.score:.2f} "
        f"center={center} size={size} residues={len(residue_ids)} "
        f"essential={','.join(essential_ids) or 'none'}{config_note}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare checked-in BioFuzz target fixtures")
    parser.add_argument("targets", nargs="*", choices=sorted(TARGET_SPECS), help="Target fixture names to build")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = args.targets or sorted(TARGET_SPECS)
    for name in selected:
        build_target(TARGET_SPECS[name])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
