from __future__ import annotations

import json
from pathlib import Path

from biofuzz.triage.record import TriageRecord


def load_findings(findings_dir: str | Path) -> list[TriageRecord]:
    findings_dir = Path(findings_dir)
    records = []
    for entry_dir in sorted(findings_dir.iterdir()):
        if not entry_dir.is_dir():
            continue
        metadata_path = entry_dir / "metadata.json"
        pose_path = entry_dir / "pose.pdbqt"
        if not metadata_path.exists() or not pose_path.exists():
            continue

        metadata = json.loads(metadata_path.read_text())
        records.append(
            TriageRecord(
                finding_id=entry_dir.name,
                finding_dir=entry_dir,
                smiles=metadata["smiles"],
                initial_affinity=metadata["affinity"],
                pose_path=pose_path,
            )
        )
    return records
