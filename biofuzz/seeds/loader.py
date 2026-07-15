from __future__ import annotations

from pathlib import Path


def load_seeds(path: str | Path) -> list[tuple[str, str]]:
    seeds = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        smiles = parts[0]
        seed_id = parts[1].strip() if len(parts) > 1 else smiles
        seeds.append((smiles, seed_id))
    return seeds
