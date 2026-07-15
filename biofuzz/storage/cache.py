from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path


class PDBQTCache:
    def __init__(self, root_dir: Path | str, max_memory_entries: int = 10000):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.max_memory_entries = max_memory_entries
        self._memory: OrderedDict[str, str] = OrderedDict()

    def _key(self, smiles: str) -> str:
        return hashlib.sha256(smiles.encode()).hexdigest()

    def _disk_path(self, key: str) -> Path:
        return self.root_dir / f"{key}.pdbqt"

    def get(self, smiles: str) -> str | None:
        key = self._key(smiles)
        if key in self._memory:
            self._memory.move_to_end(key)
            return self._memory[key]

        disk_path = self._disk_path(key)
        if disk_path.exists():
            pdbqt = disk_path.read_text()
            self._insert_memory(key, pdbqt)
            return pdbqt

        return None

    def set(self, smiles: str, pdbqt: str) -> None:
        key = self._key(smiles)
        self._disk_path(key).write_text(pdbqt)
        self._insert_memory(key, pdbqt)

    def _insert_memory(self, key: str, pdbqt: str) -> None:
        self._memory[key] = pdbqt
        self._memory.move_to_end(key)
        if len(self._memory) > self.max_memory_entries:
            self.evict_memory_lru()

    def evict_memory_lru(self) -> None:
        if self._memory:
            self._memory.popitem(last=False)
