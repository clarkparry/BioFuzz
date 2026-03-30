from __future__ import annotations

import hashlib
from pathlib import Path


class PDBQTCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, str] = {}

    @staticmethod
    def _cache_key(smiles: str) -> str:
        return hashlib.sha1(smiles.encode("utf-8")).hexdigest()

    def _cache_path(self, smiles: str) -> Path:
        return self.root / f"{self._cache_key(smiles)}.pdbqt"

    def get(self, smiles: str) -> str | None:
        if smiles in self._memory:
            return self._memory[smiles]

        path = self._cache_path(smiles)
        if not path.exists():
            return None

        text = path.read_text(encoding="utf-8")
        self._memory[smiles] = text
        return text

    def set(self, smiles: str, pdbqt_text: str) -> None:
        self._memory[smiles] = pdbqt_text
        self._cache_path(smiles).write_text(pdbqt_text, encoding="utf-8")
