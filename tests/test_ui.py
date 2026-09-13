import io
import os

from biofuzz.fuzzer import RuntimeStatus
from biofuzz.ui import FuzzerTUI, QuietUI


def test_tui_build_criterion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()

    tui = FuzzerTUI(target="test", engine="gnina", workers=1, gpu_enabled=False, stream=stream)

    status = RuntimeStatus(stage="dock", mutation_stage="havoc")
    tui.update(status)  # should not block or raise

    tui.log("[HIT] CCO | affinity=-10.5 | ...")
    tui.notice("gnina found, GPU inactive")
    tui.close()  # terminal must be restored cleanly

    assert os.path.exists("runs/test/fuzzer.log")


def test_tui_render_includes_key_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()
    tui = FuzzerTUI(target="hiv_protease", engine="gnina", workers=4, gpu_enabled=True, stream=stream)

    status = RuntimeStatus(
        stage="dock",
        mutation_stage="havoc",
        hits=3,
        corpus_size=412,
        best_affinity=-11.4,
        coverage_bitmap_occupancy=0.241,
    )
    tui.update(status)
    output = stream.getvalue()
    assert "hiv_protease" in output
    assert "GPU: active" in output
    assert "Finds: 3" in output
    assert "Corpus: 412 entries" in output

    tui.close()
    assert output  # something was written before close


def test_tui_close_restores_terminal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()
    tui = FuzzerTUI(target="test", stream=stream)
    tui.close()
    output = stream.getvalue()
    assert "\x1b[?1049l" in output  # alternate screen exited
    assert "\x1b[?25h" in output  # cursor restored
    tui.close()  # idempotent, must not raise


def test_tui_log_ring_bounded_at_10(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stream = io.StringIO()
    tui = FuzzerTUI(target="test", stream=stream)
    for i in range(15):
        tui.log(f"event {i}")
    assert len(tui._log_ring) == 10
    assert list(tui._log_ring)[0] == "event 5"  # oldest 5 evicted
    tui.close()


def test_quiet_ui_only_prints_hits():
    stream = io.StringIO()
    ui = QuietUI(stream=stream)
    ui.update(RuntimeStatus(stage="dock", mutation_stage="havoc"))
    ui.log("[HIT] CCO | affinity=-10.5")
    ui.log("routine status message")
    ui.close()
    output = stream.getvalue()
    assert "[HIT]" in output
    assert "routine status message" not in output
