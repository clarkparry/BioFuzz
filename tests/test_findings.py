from __future__ import annotations

from pathlib import Path

import pytest

from biofuzz.storage.findings import FindingsStore


def test_findings_store_requires_existing_pose_file(tmp_path: Path) -> None:
    store = FindingsStore(tmp_path / "findings")

    with pytest.raises(FileNotFoundError):
        store.save("CCO", {"is_hit": True}, tmp_path / "missing_pose.pdbqt")
