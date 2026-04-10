from __future__ import annotations

from io import StringIO

from biofuzz.core.tui import FuzzerTUI, RuntimeStatus


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

    tui.log("[HIT] test event")
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
            docks_per_sec=6.4,
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

    assert "BioFuzz AFL-style TUI" in rendered
    assert "gpu=disabled" in rendered
    assert "total_docks=128" in rendered
    assert "docks/sec=6.40" in rendered
    assert "corpus=2048" in rendered
    assert "finds=3" in rendered
    assert "mutation_type=add_substituent" in rendered
    assert "[HIT] test event" in rendered
