import os

from biofuzz.docker import DockingConfig
from biofuzz.docker.gnina import GninaBackend
from biofuzz.docker.parser import parse_log, parse_pose

HIV_RECEPTOR = "targets/hiv_protease/protein.pdbqt"
INDINAVIR_LIGAND = "targets/hiv_protease/reference_ligands/indinavir.pdbqt"


def test_gnina_available():
    backend = GninaBackend()
    assert backend.available()
    assert backend.runtime_issues() == []


def test_gnina_dock_indinavir():
    backend = GninaBackend()
    config = DockingConfig(
        receptor_path=HIV_RECEPTOR,
        center_x=13.073, center_y=22.467, center_z=5.557,
        size_x=20.0, size_y=20.0, size_z=20.0,
        exhaustiveness=4, num_modes=3, timeout_seconds=300,
        cnn_model="fast",
    )
    ligand_pdbqt = open(INDINAVIR_LIGAND).read()

    result = backend.dock(ligand_pdbqt, config)
    assert result.success
    assert result.completed
    assert result.pose_path is not None

    try:
        modes = parse_log(result.log_text)
        assert len(modes) > 0
        assert modes[0].affinity < -8.0

        atoms = parse_pose(open(result.pose_path).read())
        assert len(atoms) > 0
        assert all(not a.type.upper().startswith("H") for a in atoms)
    finally:
        os.unlink(result.pose_path)


def test_gpu_inactive_on_this_cpu_only_machine():
    backend = GninaBackend()
    config = DockingConfig(
        receptor_path=HIV_RECEPTOR,
        center_x=13.073, center_y=22.467, center_z=5.557,
        size_x=20.0, size_y=20.0, size_z=20.0,
        exhaustiveness=4, num_modes=1, timeout_seconds=300,
        cnn_model="fast",
    )
    ligand_pdbqt = open(INDINAVIR_LIGAND).read()

    result = backend.dock(ligand_pdbqt, config)
    assert result.success
    assert result.gpu_active is False

    os.unlink(result.pose_path)


def test_cpu_flag_only_passed_when_workers_greater_than_one(monkeypatch):
    backend = GninaBackend()
    captured_argv = {}

    real_popen = __import__("subprocess").Popen

    def fake_popen(argv, **kwargs):
        captured_argv["argv"] = argv
        return real_popen(argv, **kwargs)

    monkeypatch.setattr("biofuzz.docker.gnina.subprocess.Popen", fake_popen)

    config = DockingConfig(
        receptor_path=HIV_RECEPTOR,
        center_x=13.073, center_y=22.467, center_z=5.557,
        size_x=20.0, size_y=20.0, size_z=20.0,
        exhaustiveness=4, num_modes=1, timeout_seconds=300,
        cnn_model="fast",
        workers=1,
    )
    ligand_pdbqt = open(INDINAVIR_LIGAND).read()
    result = backend.dock(ligand_pdbqt, config)
    assert "--cpu" not in captured_argv["argv"]
    os.unlink(result.pose_path)

    config.workers = 2
    result = backend.dock(ligand_pdbqt, config)
    assert "--cpu" in captured_argv["argv"]
    os.unlink(result.pose_path)


def test_parse_log_from_real_gnina_output():
    log_text = """
mode |  affinity  |  intramol  |    CNN     |   CNN
     | (kcal/mol) | (kcal/mol) | pose score | affinity
-----+------------+------------+------------+----------
    1      -10.51       -1.42       0.7559      8.326
    2      -10.75       -1.27       0.7070      8.031
    3      -10.15       -1.32       0.6865      8.055
"""
    modes = parse_log(log_text)
    assert len(modes) == 3
    assert modes[0].mode == 1
    assert modes[0].affinity == -10.51
    assert modes[1].affinity == -10.75


def test_dock_process_killed_on_keyboard_interrupt(monkeypatch):
    # Real SIGINT delivery can't be exercised reliably from an automated
    # background-process test harness (backgrounded child processes have
    # SIGINT masked to SIG_IGN so they survive unrelated interrupts) --
    # this verifies the actual guarantee directly: if communicate() raises
    # for any reason (including KeyboardInterrupt), the child is killed
    # rather than orphaned.
    import biofuzz.docker.gnina as gnina_module

    killed = {"called": False}
    real_popen = gnina_module.subprocess.Popen

    class FakeProc:
        def __init__(self, *a, **k):
            self._real = real_popen(["sleep", "30"])

        def communicate(self, timeout=None):
            raise KeyboardInterrupt()

        def kill(self):
            killed["called"] = True
            self._real.kill()

        def wait(self):
            return self._real.wait()

    monkeypatch.setattr(gnina_module.subprocess, "Popen", FakeProc)

    try:
        GninaBackend._run_dock_process(["irrelevant"], timeout_seconds=5)
        assert False, "expected KeyboardInterrupt to propagate"
    except KeyboardInterrupt:
        pass

    assert killed["called"]


def test_available_false_and_runtime_issue_when_binary_missing(monkeypatch):
    import biofuzz.docker.gnina as gnina_module

    monkeypatch.setattr(gnina_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(gnina_module, "_REPO_LOCAL_GNINA", gnina_module._REPO_ROOT / "nope" / "gnina")

    backend = GninaBackend(engine_path="/nonexistent/gnina")
    assert backend.available() is False
    assert backend.runtime_issues() == ["gnina binary not found on PATH or in .tools/bin/"]
