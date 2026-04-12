from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile

from biofuzz.docking.config import BoxConfig, TargetConfig


DEFAULT_BINARIES = ("gnina", "vina", "quickvina2", "quickvina-w")
REPO_TOOL_BIN_DIR = Path(__file__).resolve().parents[2] / ".agent" / "tools" / "bin"


@dataclass
class DockingResult:
    success: bool
    log_text: str
    pose_path: str | None
    error: str | None


def _resolve_candidate_binary(candidate: str) -> str | None:
    resolved = shutil.which(candidate)
    if resolved:
        return resolved

    repo_binary = REPO_TOOL_BIN_DIR / candidate
    if repo_binary.exists() and repo_binary.is_file() and repo_binary.stat().st_mode & 0o111:
        return str(repo_binary)

    return None


def _resolve_binary(engine: str | None) -> str | None:
    if engine:
        candidates = [engine, *[item for item in DEFAULT_BINARIES if item != engine]]
    else:
        candidates = list(DEFAULT_BINARIES)
    for candidate in candidates:
        if not candidate:
            continue
        resolved = _resolve_candidate_binary(candidate)
        if resolved:
            return resolved
    return None


def _resolve_target(
    receptor_path: TargetConfig | str,
    box: BoxConfig | None,
) -> tuple[str, BoxConfig]:
    if isinstance(receptor_path, TargetConfig):
        return receptor_path.receptor, receptor_path.box

    if box is None:
        raise ValueError("`box` is required when receptor path is passed directly")

    return receptor_path, box


def dock(
    ligand_pdbqt: str,
    receptor_path: TargetConfig | str,
    box: BoxConfig | None = None,
    exhaustiveness: int = 4,
    num_modes: int = 3,
    engine: str | None = "gnina",
    timeout_seconds: int = 300,
) -> DockingResult:
    receptor_file, box_cfg = _resolve_target(receptor_path, box)
    receptor = Path(receptor_file)

    if not receptor.exists():
        return DockingResult(
            success=False,
            log_text="",
            pose_path=None,
            error=f"Receptor not found: {receptor}",
        )

    binary = _resolve_binary(engine)
    if binary is None:
        return DockingResult(
            success=False,
            log_text="",
            pose_path=None,
            error="No docking binary found. Install gnina/vina or set --engine.",
        )

    ligand_tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".pdbqt",
        prefix="biofuzz_lig_",
        delete=False,
        encoding="utf-8",
    )
    try:
        ligand_tmp.write(ligand_pdbqt)
        ligand_tmp.flush()
        ligand_path = Path(ligand_tmp.name)
    finally:
        ligand_tmp.close()

    pose_tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".pdbqt",
        prefix="biofuzz_pose_",
        delete=False,
        encoding="utf-8",
    )
    pose_path = Path(pose_tmp.name)
    pose_tmp.close()

    cmd = [
        binary,
        "--receptor",
        str(receptor),
        "--ligand",
        str(ligand_path),
        "--center_x",
        str(box_cfg.center_x),
        "--center_y",
        str(box_cfg.center_y),
        "--center_z",
        str(box_cfg.center_z),
        "--size_x",
        str(box_cfg.size_x),
        "--size_y",
        str(box_cfg.size_y),
        "--size_z",
        str(box_cfg.size_z),
        "--exhaustiveness",
        str(exhaustiveness),
        "--num_modes",
        str(num_modes),
        # Keep each docking subprocess single-threaded; the fuzzer parallelizes outside.
        "--cpu",
        "1",
        "--out",
        str(pose_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        ligand_path.unlink(missing_ok=True)
        pose_path.unlink(missing_ok=True)
        return DockingResult(
            success=False,
            log_text=exc.stdout or "",
            pose_path=None,
            error=f"Docking timed out after {timeout_seconds}s",
        )
    except Exception as exc:
        ligand_path.unlink(missing_ok=True)
        pose_path.unlink(missing_ok=True)
        return DockingResult(
            success=False,
            log_text="",
            pose_path=None,
            error=f"Docking execution failed: {exc}",
        )
    finally:
        ligand_path.unlink(missing_ok=True)

    log_text = (proc.stdout or "")
    if proc.stderr:
        log_text = f"{log_text}\n{proc.stderr}" if log_text else proc.stderr

    if proc.returncode in {-signal.SIGINT, 128 + signal.SIGINT}:
        pose_path.unlink(missing_ok=True)
        raise KeyboardInterrupt

    success = proc.returncode == 0 and pose_path.exists() and pose_path.stat().st_size > 0
    if not success:
        pose_path.unlink(missing_ok=True)
        return DockingResult(
            success=False,
            log_text=log_text,
            pose_path=None,
            error=f"Docking failed with return code {proc.returncode}",
        )

    return DockingResult(
        success=True,
        log_text=log_text,
        pose_path=str(pose_path),
        error=None,
    )
