from __future__ import annotations

from pathlib import Path

import yaml


def load_global_config(path: str | Path = "config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_target_config(target: str, targets_root: str | Path = "targets") -> dict:
    path = Path(targets_root) / target / "config.yaml"
    return yaml.safe_load(path.read_text())


def merge_defaults(global_config: dict, target_config: dict) -> dict:
    merged = dict(target_config)
    oracle = dict(global_config.get("oracle", {}))
    oracle.update(target_config.get("oracle", {}))
    merged["oracle"] = oracle
    merged["docking"] = dict(global_config.get("docking", {}))
    return merged
