from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import re

from biofuzz.docking.config import BoxConfig, TargetConfig


DEFAULT_BINARIES = ("gnina", "vina", "quickvina2", "quickvina-w")
REPO_TOOL_BIN_DIR = Path(__file__).resolve().parents[2] / ".agent" / "tools" / "bin"
_MISSING_SHARED_LIB_RE = re.compile(
    r"error while loading shared libraries:\s*(?P<lib>[^:\s]+)",
    re.IGNORECASE,
)
_LDD_MISSING_RE = re.compile(r"^\s*(?P<lib>\S+)\s*=>\s*not found\s*$", re.IGNORECASE)


@dataclass
class DockingResult:
    success: bool
    log_text: str
    pose_path: str | None
    error: str | None
    completed: bool | None = None
    gpu_active: bool | None = None


def _resolve_candidate_binary(candidate: str) -> str | None:
    resolved = shutil.which(candidate)
    if resolved:
        return resolved

    repo_binary = REPO_TOOL_BIN_DIR / candidate
    if repo_binary.exists() and repo_binary.is_file() and repo_binary.stat().st_mode & 0o111:
        return str(repo_binary)

    return None


def resolve_requested_binary(engine: str | None) -> str | None:
    if not engine:
        return None
    return _resolve_candidate_binary(engine)


def _missing_shared_libraries_from_ldd(binary_path: str, timeout_seconds: int = 5) -> list[str]:
    try:
        probe = subprocess.run(
            ["ldd", binary_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
        return []

    output = f"{probe.stdout or ''}\n{probe.stderr or ''}"
    return sorted(
        {
            match.group("lib")
            for line in output.splitlines()
            for match in [_LDD_MISSING_RE.match(line)]
            if match is not None
        }
    )


def runtime_issues_for_binary(binary_path: str, timeout_seconds: int = 5) -> list[str]:
    try:
        probe = subprocess.run(
            [binary_path, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # Process launched and stayed alive long enough to timeout; runtime dependencies are present.
        return []
    except (FileNotFoundError, PermissionError, OSError):
        return ["binary failed to execute"]

    combined_output = f"{probe.stdout or ''}\n{probe.stderr or ''}"
    missing_libs = sorted({match.group("lib") for match in _MISSING_SHARED_LIB_RE.finditer(combined_output)})
    missing_libs = sorted(set(missing_libs + _missing_shared_libraries_from_ldd(binary_path)))
    if missing_libs:
        return [f"missing shared libraries: {', '.join(missing_libs)}"]

    if probe.returncode == 126:
        return ["binary is not executable (permission denied)"]
    if probe.returncode == 127:
        return ["binary failed to start (missing runtime dependencies)"]

    return []


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


def _is_gnina_binary(binary: str) -> bool:
    return "gnina" in Path(binary).name.lower()


def _infer_gpu_active(
    *,
    binary: str,
    log_text: str,
    completed: bool,
) -> bool | None:
    if not _is_gnina_binary(binary):
        return False

    lowered = log_text.lower()
    if "warning: no gpu detected" in lowered:
        return False
    if "--no_gpu" in lowered:
        return False
    if "cnn scoring will be slow" in lowered:
        return False

    if completed:
        return True

    return None


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
            completed=False,
            gpu_active=None,
        )

    binary = _resolve_binary(engine)
    if binary is None:
        return DockingResult(
            success=False,
            log_text="",
            pose_path=None,
            error="No docking binary found. Install gnina/vina or set --engine.",
            completed=False,
            gpu_active=None,
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
            completed=False,
            gpu_active=None,
        )
    except Exception as exc:
        ligand_path.unlink(missing_ok=True)
        pose_path.unlink(missing_ok=True)
        return DockingResult(
            success=False,
            log_text="",
            pose_path=None,
            error=f"Docking execution failed: {exc}",
            completed=False,
            gpu_active=None,
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
        completed = proc.returncode == 0
        return DockingResult(
            success=False,
            log_text=log_text,
            pose_path=None,
            error=f"Docking failed with return code {proc.returncode}",
            completed=completed,
            gpu_active=_infer_gpu_active(binary=binary, log_text=log_text, completed=completed),
        )

    return DockingResult(
        success=True,
        log_text=log_text,
        pose_path=str(pose_path),
        error=None,
        completed=True,
        gpu_active=_infer_gpu_active(binary=binary, log_text=log_text, completed=True),
    )
