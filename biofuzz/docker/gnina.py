from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from biofuzz.docker import DockingBackend, DockingConfig, DockingResult

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_LOCAL_GNINA = _REPO_ROOT / ".tools" / "bin" / "gnina"


class GninaBackend(DockingBackend):
    name = "gnina"

    @staticmethod
    def _run_dock_process(argv: list[str], timeout_seconds: int) -> subprocess.CompletedProcess:
        # subprocess.run() kills the child on TimeoutExpired, but a signal
        # (e.g. Ctrl-C) arriving while blocked in communicate() raises
        # KeyboardInterrupt in the caller without necessarily killing the
        # child on every Python version -- explicit Popen + try/finally
        # guarantees no orphaned gnina process survives an interrupted dock.
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)

    def __init__(self, engine_path: str | None = None):
        self._explicit_path = engine_path
        self._resolved_path: str | None = None
        self._resolve_binary()

    def _resolve_binary(self) -> str | None:
        if self._explicit_path and os.access(self._explicit_path, os.X_OK):
            self._resolved_path = self._explicit_path
            return self._resolved_path

        path_match = shutil.which("gnina")
        if path_match:
            self._resolved_path = path_match
            return self._resolved_path

        if _REPO_LOCAL_GNINA.exists() and os.access(_REPO_LOCAL_GNINA, os.X_OK):
            self._resolved_path = str(_REPO_LOCAL_GNINA)
            return self._resolved_path

        self._resolved_path = None
        return None

    def available(self) -> bool:
        return self._resolve_binary() is not None

    def runtime_issues(self) -> list[str]:
        if not self.available():
            return ["gnina binary not found on PATH or in .tools/bin/"]
        return []

    def dock(self, ligand_pdbqt: str, config: DockingConfig) -> DockingResult:
        binary = config.engine_path or self._resolve_binary()
        if binary is None:
            return DockingResult(
                success=False,
                log_text="",
                pose_path=None,
                error="gnina binary not found",
                completed=False,
                gpu_active=None,
            )

        ligand_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".pdbqt", delete=False
        )
        try:
            ligand_file.write(ligand_pdbqt)
            ligand_file.close()

            pose_fd, pose_path = tempfile.mkstemp(suffix=".pdbqt")
            os.close(pose_fd)

            argv = [
                binary,
                "--receptor", config.receptor_path,
                "--ligand", ligand_file.name,
                "--center_x", str(config.center_x),
                "--center_y", str(config.center_y),
                "--center_z", str(config.center_z),
                "--size_x", str(config.size_x),
                "--size_y", str(config.size_y),
                "--size_z", str(config.size_z),
                "--exhaustiveness", str(config.exhaustiveness),
                "--num_modes", str(config.num_modes),
                "--out", pose_path,
            ]
            if config.workers > 1:
                argv += ["--cpu", "1"]
            if config.cnn_model:
                argv += ["--cnn", config.cnn_model]

            try:
                proc = self._run_dock_process(argv, config.timeout_seconds)
            except subprocess.TimeoutExpired:
                return DockingResult(
                    success=False,
                    log_text="",
                    pose_path=None,
                    error=f"docking timed out after {config.timeout_seconds}s",
                    completed=False,
                    gpu_active=None,
                )

            log_text = (proc.stdout or "") + (proc.stderr or "")
            gpu_active = _detect_gpu_active(log_text)

            if proc.returncode != 0:
                return DockingResult(
                    success=False,
                    log_text=log_text,
                    pose_path=None,
                    error=proc.stderr,
                    completed=True,
                    gpu_active=gpu_active,
                )

            return DockingResult(
                success=True,
                log_text=log_text,
                pose_path=pose_path,
                error=None,
                completed=True,
                gpu_active=gpu_active,
            )
        finally:
            try:
                os.unlink(ligand_file.name)
            except FileNotFoundError:
                pass


def _detect_gpu_active(log_text: str) -> bool | None:
    """Whether gnina used a GPU, inferred from its banner.

    gnina announces the absence of a GPU ("WARNING: No GPU detected.") but says
    nothing when one is present, so presence can only be inferred from silence.
    An empty or truncated log is reported as unknown (None) rather than as a
    GPU run, since there is no evidence either way.
    """
    if not log_text.strip():
        return None
    lowered = log_text.lower()
    if "no gpu detected" in lowered or "--no_gpu" in lowered:
        return False
    return True
