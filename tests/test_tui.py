from __future__ import annotations

from io import StringIO
import re

import pytest

from biofuzz.core.tui import FuzzerTUI, RuntimeStatus


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class FakeTTY(StringIO):
    def isatty(self) -> bool:
        return True


def test_tui_renders_requested_metrics() -> None:
    stream = FakeTTY()
    tui = FuzzerTUI(
        target="egfr",
        engine="vina",
        workers=2,
        gpu_enabled=False,
        stream=stream,
        refresh_seconds=0.0,
        enabled=True,
    )

    tui.log("[CHKPT] iterations=100")
    tui.notice("gnina not installed; using vina (CPU-only).")
    tui.log("[HIT] CCN | affinity=-10.20 | confirmed_exhaustiveness=16")
    tui.update(
        RuntimeStatus(
            stage="dock",
            mutation_stage="havoc",
            mutation_type="add_substituent",
            current_parent="CCO",
            current_smiles="CCN",
            power_score=12.5,
            mutation_budget=40,
            total_docks=128,
            completed_docks=120,
            docks_per_sec=6.4,
            completed_dock_staleness_seconds=0.0,
            corpus_size=2048,
            finds=3,
            coverage_ratio=0.625,
            best_affinity=-10.2,
            checkpoints=2,
            elapsed_seconds=95.0,
        )
    )
    tui.close()

    rendered = stream.getvalue()

    assert "BioFuzz :: egfr" in rendered
    assert "Process Info" in rendered
    assert "Overall Results" in rendered
    assert "Progress" in rendered
    assert "Findings In Depth" in rendered
    assert "State" in rendered
    assert "Run Log" in rendered
    assert "Attempted Docks" in rendered
    assert "Completed Docks" in rendered
    assert "Docks/sec" in rendered
    assert "Corpus Size" in rendered
    assert "Mutation Type" in rendered
    assert "CCN" in rendered
    assert "-10.20" in rendered
    assert "[INFO] gnina not installed; using vina (CPU-only)." in rendered
    assert "[CHKPT]" not in rendered
    assert "Recent events" not in rendered
    assert "\x1b[?1049h" in rendered
    assert "\x1b[?1049l" in rendered


@pytest.mark.parametrize(
    ("staleness", "expected_code"),
    [
        (20.0, "\x1b[1;33mCompleted Docks"),
        (40.0, "\x1b[1;38;5;208mCompleted Docks"),
        (65.0, "\x1b[1;31mCompleted Docks"),
    ],
)
def test_tui_colors_completed_docks_when_stalled(staleness: float, expected_code: str) -> None:
    stream = FakeTTY()
    tui = FuzzerTUI(
        target="egfr",
        engine="vina",
        workers=2,
        gpu_enabled=False,
        stream=stream,
        refresh_seconds=0.0,
        enabled=True,
    )

    tui.update(
        RuntimeStatus(
            stage="dock",
            mutation_stage="havoc",
            mutation_type="add_substituent",
            current_parent="CCO",
            current_smiles="CCN",
            power_score=12.5,
            mutation_budget=40,
            total_docks=128,
            completed_docks=64,
            docks_per_sec=3.2,
            completed_dock_staleness_seconds=staleness,
            corpus_size=2048,
            finds=3,
            coverage_ratio=0.625,
            best_affinity=-10.2,
            checkpoints=2,
            elapsed_seconds=95.0,
        )
    )
    tui.close()

    rendered = stream.getvalue()
    assert expected_code in rendered


@pytest.mark.parametrize(
    ("gpu_active", "expected_label"),
    [
        (True, "GPU: active"),
        (False, "GPU: inactive"),
        (None, "GPU: probing"),
    ],
)
def test_tui_reports_runtime_gpu_activity(
    gpu_active: bool | None,
    expected_label: str,
) -> None:
    stream = FakeTTY()
    tui = FuzzerTUI(
        target="egfr",
        engine="gnina",
        workers=2,
        gpu_enabled=True,
        stream=stream,
        refresh_seconds=0.0,
        enabled=True,
    )

    tui.update(
        RuntimeStatus(
            stage="dock",
            mutation_stage="havoc",
            mutation_type="add_substituent",
            current_parent="CCO",
            current_smiles="CCN",
            power_score=12.5,
            mutation_budget=40,
            total_docks=8,
            completed_docks=8,
            docks_per_sec=1.0,
            completed_dock_staleness_seconds=0.0,
            corpus_size=2,
            finds=0,
            coverage_ratio=0.1,
            best_affinity=-8.0,
            checkpoints=0,
            elapsed_seconds=5.0,
            gpu_active=gpu_active,
        )
    )
    tui.close()

    rendered = ANSI_RE.sub("", stream.getvalue())
    assert expected_label in rendered
