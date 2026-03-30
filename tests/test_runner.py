from __future__ import annotations

from pathlib import Path

from biofuzz.docking.config import BoxConfig
from biofuzz.docking import runner


def test_resolve_binary_falls_back_from_requested_engine(monkeypatch) -> None:
    calls: list[str] = []

    def fake_which(name: str) -> str | None:
        calls.append(name)
        if name == "vina":
            return "/usr/bin/vina"
        return None

    monkeypatch.setattr(runner.shutil, "which", fake_which)
    resolved = runner._resolve_binary("gnina")

    assert resolved == "/usr/bin/vina"
    assert calls[0] == "gnina"
    assert "vina" in calls


def test_resolve_binary_checks_default_order(monkeypatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/quickvina2" if name == "quickvina2" else None

    monkeypatch.setattr(runner.shutil, "which", fake_which)
    monkeypatch.setattr(runner, "REPO_TOOL_BIN_DIR", Path("/definitely/missing/biofuzz-tool-bin"))
    assert runner._resolve_binary(None) == "/usr/bin/quickvina2"


def test_resolve_binary_checks_repo_local_tool_dir(monkeypatch, tmp_path: Path) -> None:
    local_vina = tmp_path / "vina"
    local_vina.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    local_vina.chmod(0o755)

    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runner, "REPO_TOOL_BIN_DIR", tmp_path)

    assert runner._resolve_binary("gnina") == str(local_vina)


def test_dock_supports_receptor_path_keyword(monkeypatch, tmp_path: Path) -> None:
    receptor = tmp_path / "receptor.pdbqt"
    receptor.write_text("RECEPTOR", encoding="utf-8")
    box = BoxConfig(center_x=0.0, center_y=0.0, center_z=0.0, size_x=10.0, size_y=10.0, size_z=10.0)

    monkeypatch.setattr(runner, "_resolve_binary", lambda engine: "/usr/bin/mock")

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        assert "--cpu" in cmd
        assert cmd[cmd.index("--cpu") + 1] == "1"
        out_idx = cmd.index("--out") + 1
        Path(cmd[out_idx]).write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.dock(
        ligand_pdbqt="ATOM      1  C1  LIG A   1       0.0   0.0   0.0  0.00  0.00   0.00 C\n",
        receptor_path=str(receptor),
        box=box,
    )

    assert result.success
    assert result.pose_path is not None
