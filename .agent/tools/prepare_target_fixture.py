#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
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
from biofuzz.protein.residues import residue_key

# Reference-ligand prep only needs a valid embeddable 3D structure, not the
# drug-likeness bounds applied to fuzzed candidates — bounds are wide open
# so real approved drugs (e.g. indinavir, MW ~614) don't get rejected.
REFERENCE_LIGAND_BOUNDS = dict(
    min_mw=0.0, max_mw=2000.0, max_logp=100.0, max_hbd=100, max_hba=100, max_rot_bonds=100,
)


@dataclass(frozen=True)
class TargetSpec:
    name: str
    pdb_id: str
    protein_chains: tuple[str, ...]
    ligand_code: str
    inhibitor_name: str


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
        # A/B interface, so both chains are needed for a complete pocket.
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


def parse_structure(
    pdb_path: Path,
    protein_chains: tuple[str, ...],
    ligand_code: str,
) -> tuple[list[str], list[tuple[str, int, float, float, float]], list[tuple[float, float, float]]]:
    receptor_lines: list[str] = []
    receptor_atoms: list[tuple[str, int, float, float, float]] = []
    ligand_atoms: list[tuple[float, float, float]] = []

    for raw_line in pdb_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.startswith(("ATOM  ", "HETATM")):
            continue
        altloc = raw_line[16]
        if altloc not in {" ", "A"}:
            continue
        chain_id = raw_line[21].strip()
        if chain_id not in protein_chains:
            continue

        resname = raw_line[17:20].strip()
        element = raw_line[76:78].strip() or raw_line[12:16].strip()[:1]
        x = float(raw_line[30:38])
        y = float(raw_line[38:46])
        z = float(raw_line[46:54])

        if raw_line.startswith("ATOM  "):
            receptor_lines.append(raw_line)
            if element != "H":
                receptor_atoms.append((chain_id, int(raw_line[22:26]), x, y, z))
        elif resname == ligand_code and element != "H":
            ligand_atoms.append((x, y, z))

    if not receptor_lines:
        raise ValueError(f"No receptor atoms found in chains {protein_chains} for {pdb_path}")
    if not ligand_atoms:
        raise ValueError(f"No ligand atoms found for {ligand_code} in chains {protein_chains} for {pdb_path}")

    return receptor_lines, receptor_atoms, ligand_atoms


def compute_box(ligand_atoms: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    xs = [atom[0] for atom in ligand_atoms]
    ys = [atom[1] for atom in ligand_atoms]
    zs = [atom[2] for atom in ligand_atoms]
    center = (
        round((min(xs) + max(xs)) / 2.0, 3),
        round((min(ys) + max(ys)) / 2.0, 3),
        round((min(zs) + max(zs)) / 2.0, 3),
    )

    def dimension(values: list[float]) -> float:
        extent = max(values) - min(values)
        return round(max(18.0, min(28.0, extent + 8.0)), 3)

    size = (dimension(xs), dimension(ys), dimension(zs))
    return center, size


def compute_pocket_residues(
    receptor_atoms: list[tuple[str, int, float, float, float]],
    ligand_atoms: list[tuple[float, float, float]],
    cutoff: float = 4.5,
) -> list[str]:
    cutoff_sq = cutoff * cutoff
    residues: set[str] = set()
    for chain_id, residue_id, x, y, z in receptor_atoms:
        for lx, ly, lz in ligand_atoms:
            distance_sq = ((x - lx) ** 2) + ((y - ly) ** 2) + ((z - lz) ** 2)
            if distance_sq <= cutoff_sq:
                residues.add(residue_key(chain_id, residue_id))
                break
    return sorted(
        residues,
        key=lambda value: (
            value.split(":", 1)[0],
            int(value.split(":", 1)[1]),
        ),
    )


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


def write_config(
    spec: TargetSpec,
    target_dir: Path,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    residue_ids: list[str],
) -> None:
    residue_lines = "\n".join(f'    - "{residue_id}"' for residue_id in residue_ids)
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
  residue_ids:
{residue_lines}
'''
    (target_dir / "config.yaml").write_text(config_text, encoding="utf-8")


def build_target(spec: TargetSpec) -> None:
    build_dir = BUILD_ROOT / spec.name
    build_dir.mkdir(parents=True, exist_ok=True)

    pdb_path = fetch(f"https://files.rcsb.org/download/{spec.pdb_id}.pdb", build_dir / f"{spec.pdb_id}.pdb")
    ligand_cif = fetch(
        f"https://files.rcsb.org/ligands/download/{spec.ligand_code}.cif",
        build_dir / f"{spec.ligand_code}.cif",
    )
    smiles = parse_ligand_smiles(ligand_cif)
    receptor_lines, receptor_atoms, ligand_atoms = parse_structure(
        pdb_path,
        spec.protein_chains,
        spec.ligand_code,
    )
    center, size = compute_box(ligand_atoms)
    residue_ids = compute_pocket_residues(receptor_atoms, ligand_atoms)

    target_dir = TARGETS_ROOT / spec.name
    reference_dir = target_dir / "reference_ligands"
    reference_dir.mkdir(parents=True, exist_ok=True)

    receptor_pdb = write_receptor_pdb(receptor_lines, build_dir / f"{spec.name}_receptor.pdb")
    prepare_receptor_pdbqt(receptor_pdb, target_dir / "protein.pdbqt")
    write_config(spec, target_dir, center, size, residue_ids)

    ligand_slug = spec.inhibitor_name.replace("-", "_")
    (reference_dir / f"{ligand_slug}.smi").write_text(
        f"{smiles} {spec.inhibitor_name}\n",
        encoding="utf-8",
    )
    ligand_pdbqt = prepare_smiles(smiles, **REFERENCE_LIGAND_BOUNDS)
    if ligand_pdbqt is None:
        raise ValueError(f"Failed to prepare reference ligand for {spec.name}: {spec.inhibitor_name}")
    (reference_dir / f"{ligand_slug}.pdbqt").write_text(ligand_pdbqt, encoding="utf-8")

    print(
        f"prepared {spec.name}: pdb={spec.pdb_id} chains={spec.protein_chains} ligand={spec.ligand_code} "
        f"center={center} size={size} residues={len(residue_ids)}"
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
