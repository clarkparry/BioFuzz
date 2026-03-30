from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any


class FindingsStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _entry_id(self, smiles: str) -> str:
        digest = hashlib.sha1(smiles.encode("utf-8")).hexdigest()[:12]
        return f"{time.time_ns()}_{digest}"

    def save(self, smiles: str, verdict: Any, pose_path: str | Path) -> Path:
        entry_id = self._entry_id(smiles)
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
