from biofuzz.docking.config import BoxConfig, OracleConfig, PocketConfig, TargetConfig
from biofuzz.docking.parser import DockingMode
from biofuzz.docking.runner import DockingResult
from biofuzz.oracle import affinity as affinity_oracle
from biofuzz.oracle import selectivity as selectivity_oracle
from biofuzz.oracle.affinity import evaluate, passes_affinity


def _target_with_offtarget() -> TargetConfig:
    box = BoxConfig(
        center_x=0.0,
        center_y=0.0,
        center_z=0.0,
        size_x=10.0,
        size_y=10.0,
        size_z=10.0,
    )
    return TargetConfig(
        name="test_target",
        receptor="target.pdbqt",
        box=box,
        pocket=PocketConfig(residue_ids={1, 2, 3}, contact_cutoff=3.5),
        oracle=OracleConfig(
            affinity_threshold=-9.0,
            strain_threshold=3.5,
            selectivity_ratio_min=2.0,
        ),
        offtarget_receptor="offtarget.pdbqt",
        offtarget_box=box,
    )


def test_passes_affinity_true_for_strong_binder() -> None:
    modes = [DockingMode(mode=1, affinity=-10.2, rmsd_lb=0.0, rmsd_ub=0.0)]
    assert passes_affinity(modes, threshold=-9.0)


def test_evaluate_rejects_weak_binder() -> None:
    modes = [DockingMode(mode=1, affinity=-7.1, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK strain 1.2\n"
    verdict = evaluate(modes, pose, OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5))
    assert not verdict.is_hit
    assert verdict.selectivity_status == "not_configured"


def test_evaluate_rejects_high_strain() -> None:
    modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 5.6\n"
    verdict = evaluate(modes, pose, OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5))
    assert not verdict.is_hit
    assert "strain" in verdict.notes.lower()
    assert verdict.selectivity_status == "not_configured"


def test_evaluate_accepts_good_affinity_and_strain() -> None:
    modes = [DockingMode(mode=1, affinity=-10.5, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 1.1\n"
    verdict = evaluate(modes, pose, OracleConfig(affinity_threshold=-9.0, strain_threshold=3.5))
    assert verdict.is_hit
    assert verdict.affinity < -9.0
    assert verdict.selectivity_status == "not_configured"


def test_evaluate_skips_selectivity_when_affinity_fails(monkeypatch) -> None:
    calls: list[str] = []

    def fake_selectivity(smiles: str, target: TargetConfig, **kwargs) -> float | None:
        calls.append(smiles)
        return 5.0

    monkeypatch.setattr(affinity_oracle, "selectivity_ratio_from_target", fake_selectivity)

    modes = [DockingMode(mode=1, affinity=-7.2, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 1.0\n"
    verdict = evaluate(modes, pose, _target_with_offtarget(), smiles="CCO")

    assert not verdict.is_hit
    assert calls == []


def test_evaluate_applies_selectivity_after_affinity_pass(monkeypatch) -> None:
    calls: list[str] = []

    def fake_selectivity(smiles: str, target: TargetConfig, **kwargs) -> float | None:
        calls.append(smiles)
        assert kwargs["ligand_pdbqt"] == "PDBQT"
        assert kwargs["target_affinity"] == -10.3
        assert kwargs["exhaustiveness"] == 12
        assert kwargs["num_modes"] == 5
        assert kwargs["engine"] == "vina"
        return 1.5

    monkeypatch.setattr(affinity_oracle, "selectivity_ratio_from_target", fake_selectivity)

    modes = [DockingMode(mode=1, affinity=-10.3, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 0.9\n"
    verdict = evaluate(
        modes,
        pose,
        _target_with_offtarget(),
        smiles="CCO",
        ligand_pdbqt="PDBQT",
        selectivity_exhaustiveness=12,
        num_modes=5,
        docking_engine="vina",
    )

    assert calls == ["CCO"]
    assert not verdict.is_hit
    assert verdict.selectivity_status == "failed"
    assert "Selectivity ratio below threshold" in verdict.notes


def test_evaluate_skips_selectivity_when_redock_unavailable(monkeypatch) -> None:
    def fake_selectivity(smiles: str, target: TargetConfig, **kwargs) -> float | None:
        return None

    monkeypatch.setattr(affinity_oracle, "selectivity_ratio_from_target", fake_selectivity)

    modes = [DockingMode(mode=1, affinity=-10.3, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 0.9\n"
    verdict = evaluate(modes, pose, _target_with_offtarget(), smiles="CCO")

    assert verdict.is_hit
    assert verdict.selectivity_status == "skipped_unavailable"
    assert "Selectivity skipped" in verdict.notes


def test_evaluate_can_fail_closed_when_selectivity_is_unavailable(monkeypatch) -> None:
    def fake_selectivity(smiles: str, target: TargetConfig, **kwargs) -> float | None:
        return None

    monkeypatch.setattr(affinity_oracle, "selectivity_ratio_from_target", fake_selectivity)

    modes = [DockingMode(mode=1, affinity=-10.3, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 0.9\n"
    target = _target_with_offtarget()
    target = TargetConfig(
        name=target.name,
        receptor=target.receptor,
        box=target.box,
        pocket=target.pocket,
        oracle=OracleConfig(
            affinity_threshold=-9.0,
            strain_threshold=3.5,
            selectivity_ratio_min=2.0,
            selectivity_policy="fail_closed",
        ),
        offtarget_receptor=target.offtarget_receptor,
        offtarget_box=target.offtarget_box,
    )

    verdict = evaluate(modes, pose, target, smiles="CCO")

    assert not verdict.is_hit
    assert verdict.selectivity_status == "skipped_unavailable"
    assert "fail_closed" in verdict.notes


def test_evaluate_can_skip_selectivity_until_confirmation(monkeypatch) -> None:
    calls: list[str] = []

    def fake_selectivity(smiles: str, target: TargetConfig, **kwargs) -> float | None:
        calls.append(smiles)
        return 3.0

    monkeypatch.setattr(affinity_oracle, "selectivity_ratio_from_target", fake_selectivity)

    modes = [DockingMode(mode=1, affinity=-10.3, rmsd_lb=0.0, rmsd_ub=0.0)]
    pose = "REMARK internal strain 0.9\n"
    verdict = evaluate(
        modes,
        pose,
        _target_with_offtarget(),
        smiles="CCO",
        check_selectivity=False,
    )

    assert verdict.is_hit
    assert calls == []
    assert verdict.selectivity_status == "not_configured"


def test_selectivity_best_affinity_cleans_pose_file(monkeypatch, tmp_path) -> None:
    target = _target_with_offtarget()
    pose_path = tmp_path / "pose.pdbqt"
    pose_path.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

    def fake_prepare_smiles(smiles: str) -> str:
        assert smiles == "CCO"
        return "PDBQT"

    def fake_dock(*args, **kwargs) -> DockingResult:
        return DockingResult(
            success=True,
            log_text="   1       -8.0      0.000      0.000\n",
            pose_path=str(pose_path),
            error=None,
        )

    monkeypatch.setattr(selectivity_oracle, "prepare_smiles", fake_prepare_smiles)
    monkeypatch.setattr(selectivity_oracle, "dock", fake_dock)

    affinity = selectivity_oracle._best_affinity("CCO", target)
    assert affinity == -8.0
    assert not pose_path.exists()
