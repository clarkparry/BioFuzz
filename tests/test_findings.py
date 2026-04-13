from __future__ import annotations

from pathlib import Path

import pytest

from biofuzz.storage.findings import FindingsStore


def test_findings_store_requires_existing_pose_file(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path / "findings")

    with pytest.raises(FileNotFoundError):
        store.save("CCO", {"is_hit": True}, tmp_path / "missing_pose.pdbqt", -9.5)


def test_findings_store_names_findings_with_id_timestamp_and_affinity(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path / "findings")
    pose_path = tmp_path / "pose.pdbqt"
    pose_path.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

    first = store.save("CCO", {"is_hit": True}, pose_path, -10.25)
    second = store.save("CCN", {"is_hit": True}, pose_path, -9.5)

    first_id, first_timestamp, first_affinity = first.name.split("_")
    second_id, second_timestamp, second_affinity = second.name.split("_")

    assert first_id == "000001"
    assert second_id == "000002"
    assert len(first_timestamp) == 15
    assert len(second_timestamp) == 15
    assert first_affinity == "-10.25"
    assert second_affinity == "-9.50"
