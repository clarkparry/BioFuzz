from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import math
from pathlib import Path
import shutil
import time
from typing import Any


class FindingsStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _next_finding_id(self) -> int:
        max_id = 0
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            entry_id = path.name.split("_", 1)[0]
            if entry_id.isdigit():
                max_id = max(max_id, int(entry_id))
        return max_id + 1

    def _entry_id(self, finding_id: int, confirmed_affinity: float) -> str:
        timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
        affinity = float(confirmed_affinity)
        affinity_token = (
            f"{affinity:.2f}" if math.isfinite(affinity) else "unknown_affinity"
        )
        return f"{finding_id:06d}_{timestamp}_{affinity_token}"

    def save(
        self,
        smiles: str,
        verdict: Any,
        pose_path: str | Path,
        confirmed_affinity: float,
    ) -> Path:
        entry_id = self._entry_id(
            finding_id=self._next_finding_id(),
            confirmed_affinity=confirmed_affinity,
        )
        entry_dir = self.root / entry_id
        entry_dir.mkdir(parents=True, exist_ok=True)

        pose_src = Path(pose_path)
        pose_dst = entry_dir / "pose.pdbqt"
        if not pose_src.exists():
            raise FileNotFoundError(f"Pose file not found: {pose_src}")
        shutil.copyfile(pose_src, pose_dst)

        if is_dataclass(verdict):
            verdict_payload = asdict(verdict)
        elif isinstance(verdict, dict):
            verdict_payload = verdict
        else:
            verdict_payload = {"value": str(verdict)}

        metadata = {
            "smiles": smiles,
            "verdict": verdict_payload,
            "pose_path": str(pose_dst),
        }
        (entry_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        return entry_dir
