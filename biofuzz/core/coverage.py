from __future__ import annotations

from pathlib import Path
import json


class CoverageMap:
    def __init__(self, pocket_residue_ids: set[int] | list[int] | tuple[int, ...]):
        self.pocket_residue_ids: set[int] = {int(rid) for rid in pocket_residue_ids}
        self.global_coverage: set[int] = set()

    def update(self, fingerprint: frozenset[int]) -> frozenset[int]:
        valid_bits = {int(rid) for rid in fingerprint if int(rid) in self.pocket_residue_ids}
        new_bits = valid_bits - self.global_coverage
        self.global_coverage.update(valid_bits)
        return frozenset(new_bits)

    def coverage_ratio(self) -> float:
        if not self.pocket_residue_ids:
            return 0.0
        return len(self.global_coverage) / len(self.pocket_residue_ids)

    def save(self, path: str | Path) -> None:
        payload = {
            "pocket_residue_ids": sorted(self.pocket_residue_ids),
            "global_coverage": sorted(self.global_coverage),
        }
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.pocket_residue_ids = {int(v) for v in payload.get("pocket_residue_ids", [])}
        self.global_coverage = {
            int(v)
            for v in payload.get("global_coverage", [])
            if int(v) in self.pocket_residue_ids
        }
