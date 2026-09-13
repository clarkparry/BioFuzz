from __future__ import annotations

from pathlib import Path

import yaml


def load_global_config(path: str | Path = "config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_target_config(target: str, targets_root: str | Path = "targets") -> dict:
    path = Path(targets_root) / target / "config.yaml"
    return yaml.safe_load(path.read_text())


def _merge_section(global_config: dict, target_config: dict, section: str) -> dict:
    merged = dict(global_config.get(section, {}))
    merged.update(target_config.get(section, {}))
    return merged


MERGED_SECTIONS = ("oracle", "docking", "triage", "molecules")


def merge_defaults(global_config: dict, target_config: dict) -> dict:
    """Overlay a target's config onto the global defaults, section by section.

    Every section in MERGED_SECTIONS is *merged* rather than replaced, so a
    target can override one key without restating the whole section.

    `molecules` is overlaid because viable chemical space is target-dependent.
    The global max_mw of 550 is a sensible drug-like bound, but HIV protease
    inhibitors are legitimately 600-720 Da. At 550 the preparation step rejects
    that entire class before docking, so no campaign against such a target could
    generate anything resembling what works on it. See docs/adding_targets.md.
    """
    merged = dict(target_config)
    for section in MERGED_SECTIONS:
        merged[section] = _merge_section(global_config, target_config, section)
    return merged
