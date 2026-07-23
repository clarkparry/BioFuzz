from __future__ import annotations

import fcntl
import json
import shutil
from datetime import datetime
from pathlib import Path


class FindingsStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._counter_path = self.root / "counter.json"
        if not self._counter_path.exists():
            self._counter_path.write_text("0")
        self._seen_smiles: set[str] = set()
        self._reload_seen()

    def _reload_seen(self) -> None:
        """Index molecules already on disk so a resumed run doesn't re-save them."""
        for metadata_path in self.root.glob("*/metadata.json"):
            try:
                smiles = json.loads(metadata_path.read_text()).get("smiles")
            except (OSError, ValueError):
                continue
            if smiles:
                self._seen_smiles.add(smiles)

    def already_saved(self, smiles: str) -> bool:
        return smiles in self._seen_smiles

    def _next_id(self) -> int:
        with open(self._counter_path, "r+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                current = int(fh.read().strip() or "0")
                next_id = current + 1
                fh.seek(0)
                fh.truncate()
                fh.write(str(next_id))
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return next_id

    def save(
        self,
        smiles: str,
        verdict: dict,
        pose_path: Path | str,
        affinity: float,
        strain: float | None = None,
        passed_tiers: list[str] | None = None,
        notes: str | None = None,
        mutation_stage: str | None = None,
        mutation_type: str | None = None,
        parent_smiles: str | None = None,
        mutation_lineage: list[str] | None = None,
        corpus_source_id: str | None = None,
        timestamp: datetime | None = None,
        ligand_efficiency: float | None = None,
        vina_affinity: float | None = None,
        cnn_affinity_kcal: float | None = None,
        cnn_pose_score: float | None = None,
        heavy_atom_count: int | None = None,
        scoring_policy: str | None = None,
        novelty_class: str | None = None,
        new_coverage_bits: int | None = None,
        dedupe: bool = True,
    ) -> Path | None:
        """Persist a hit. Returns None when `dedupe` suppressed a repeat.

        The same molecule can be rediscovered many times -- a mutation that
        reverts, or two parents converging on one product. The reference
        campaign saved one molecule under two separate finding IDs, which
        double-counted the hit and made triage re-dock it twice.
        """
        if dedupe and smiles in self._seen_smiles:
            return None

        finding_id = self._next_id()
        ts = timestamp or datetime.now()

        dir_name = f"{finding_id:06d}_{ts.strftime('%Y%m%dT%H%M%S')}_{affinity:.2f}"
        finding_dir = self.root / dir_name
        finding_dir.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(pose_path, finding_dir / "pose.pdbqt")

        metadata = {
            "smiles": smiles,
            "affinity": affinity,
            "strain": strain if strain is not None else verdict.get("strain"),
            "passed_tiers": passed_tiers if passed_tiers is not None else verdict.get("passed_tiers", []),
            "notes": notes if notes is not None else verdict.get("notes", ""),
            "mutation_stage": mutation_stage,
            "mutation_type": mutation_type,
            "parent_smiles": parent_smiles,
            "mutation_lineage": mutation_lineage or [],
            "corpus_source_id": corpus_source_id,
            "timestamp": ts.isoformat(),
            "ligand_efficiency": ligand_efficiency,
            "heavy_atom_count": heavy_atom_count,
            "vina_affinity": vina_affinity,
            "cnn_affinity_kcal": cnn_affinity_kcal,
            "cnn_pose_score": cnn_pose_score,
            "scoring_policy": scoring_policy,
            "novelty_class": novelty_class,
            "new_coverage_bits": new_coverage_bits,
        }
        (finding_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        self._seen_smiles.add(smiles)

        return finding_dir

    save_finding = save
