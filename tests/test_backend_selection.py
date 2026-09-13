"""The docking engine named in config must be the one that runs.

`docking.engine` is documented as the way to swap engines without touching the
fuzzer. That only holds if the campaign resolves the backend through the
registry instead of importing one directly, so these tests pin the contract.
"""

import pytest

from biofuzz.docker import DockingBackend, DockingConfig, DockingResult, get_backend
from biofuzz.docker.gnina import GninaBackend, _detect_gpu_active
from biofuzz.fuzzer.worker import dock_worker


def test_get_backend_returns_the_named_engine():
    backend = get_backend("gnina")
    assert isinstance(backend, GninaBackend)
    assert backend.name == "gnina"


def test_get_backend_rejects_an_unknown_engine():
    with pytest.raises(ValueError, match="Unknown docking backend"):
        get_backend("autodock-vina")


def test_gnina_backend_satisfies_the_abstract_contract():
    # The fuzzer imports only DockingBackend, so any registered engine has to
    # be substitutable for it.
    assert issubclass(GninaBackend, DockingBackend)
    for method in ("dock", "available", "runtime_issues"):
        assert callable(getattr(GninaBackend, method))


def test_dock_worker_dispatches_on_the_engine_name(monkeypatch):
    """The worker process resolves the engine by name, not by hardcoded import."""
    calls = []

    class RecordingBackend(DockingBackend):
        name = "recording"

        def dock(self, ligand_pdbqt, config):
            calls.append(ligand_pdbqt)
            return DockingResult(
                success=True, log_text="", pose_path=None, error=None,
                completed=True, gpu_active=None,
            )

        def available(self):
            return True

        def runtime_issues(self):
            return []

    monkeypatch.setattr(
        "biofuzz.fuzzer.worker.get_backend", lambda name: RecordingBackend()
    )
    config = DockingConfig(
        receptor_path="r.pdbqt",
        center_x=0.0, center_y=0.0, center_z=0.0,
        size_x=20.0, size_y=20.0, size_z=20.0,
        exhaustiveness=8, num_modes=3, timeout_seconds=60,
    )
    result = dock_worker("LIGAND", config, engine="recording")
    assert result.success
    assert calls == ["LIGAND"]


def test_campaign_reads_the_engine_from_config():
    from biofuzz.fuzzer.config import load_global_config, load_target_config, merge_defaults

    global_config = load_global_config("config.yaml")
    merged = merge_defaults(global_config, load_target_config("hiv_protease"))
    # A campaign takes its engine from this key; if the key vanished, the
    # campaign would silently fall back to a default and the config would lie.
    assert merged["docking"]["engine"] == "gnina"


# --- GPU reporting ------------------------------------------------------------


def test_gpu_inactive_when_gnina_says_no_gpu():
    log = "WARNING: No GPU detected. CNN scoring will be slow.\n"
    assert _detect_gpu_active(log) is False


def test_gpu_unknown_when_there_is_no_log_to_read():
    """Absence of evidence is not evidence of a GPU.

    gnina announces a missing GPU but says nothing when one is present, so
    presence can only be inferred from silence. An empty log carries no such
    silence to interpret and must report unknown rather than 'active'.
    """
    assert _detect_gpu_active("") is None
    assert _detect_gpu_active("   \n ") is None


def test_gpu_active_inferred_from_a_real_log_without_a_warning():
    log = "gnina v1.3.2\nUsing random seed: 42\nmode | affinity\n 1 -9.0\n"
    assert _detect_gpu_active(log) is True
