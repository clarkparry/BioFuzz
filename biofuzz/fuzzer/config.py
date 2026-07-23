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

    Every section in MERGED_SECTIONS is *merged*, not replaced. `docking` used to
    be assigned straight from the global config, which silently discarded any
    per-target docking override -- a target could set its own exhaustiveness or
    CNN model and the fuzzer would ignore it.

    `molecules` is overlaid because viable chemical space is target-dependent.
    The global max_mw of 550 is a sensible drug-like bound, but HIV protease
    inhibitors are legitimately 600-720 Da: at 550 the fuzzer silently rejects
    indinavir -- hiv_protease's own reference drug and target-specific seed --
    before it can ever be docked, so nothing of the chemical class that actually
    works on that target could be found. See docs/evaluation_2026-07.md §D2.
    """
    merged = dict(target_config)
    for section in MERGED_SECTIONS:
        merged[section] = _merge_section(global_config, target_config, section)
    return merged
