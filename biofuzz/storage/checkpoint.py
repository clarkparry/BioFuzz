from __future__ import annotations

import json
from pathlib import Path


class CheckpointVersionError(Exception):
    pass


def save_checkpoint(path: Path | str, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def load_checkpoint(path: Path | str) -> dict:
    return json.loads(Path(path).read_text())


def load_coverage_checkpoint(path: Path | str, expected_version: int) -> dict:
    data = load_checkpoint(path)
    if data.get("version") != expected_version:
        raise CheckpointVersionError(
            f"checkpoint version {data.get('version')} != expected {expected_version}"
        )
    return data
