from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import importlib.util
import copy
import types
from typing import Any, Iterable, Mapping

from biofuzz.core.coverage import (
    DEFAULT_COVERAGE_MAP_SIZE_KIB,
    DEFAULT_COVERAGE_MODE,
    DEFAULT_OCCUPANCY_ROTATE_THRESHOLD,
    normalize_novelty_weights,
    validate_coverage_settings,
)
from biofuzz.protein.residue_keys import (
    is_qualified_residue_key,
    normalize_residue_keys,
    residue_number,
    sort_residue_keys,
)

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency in runtime environments
    yaml = None  # type: ignore[assignment]


DEFAULT_GLOBAL_CONFIG: dict[str, Any] = {
    "docking": {
        "exhaustiveness_fuzz": 4,
        "exhaustiveness_confirm": 16,
        "num_modes": 3,
        "engine": "gnina",
    },
    "oracle": {
        "affinity_threshold": -9.0,
        "strain_threshold": 3.5,
        "selectivity_ratio_min": 2.0,
        "selectivity_policy": "fail_open",
    },
    "molecules": {
        "max_mw": 550,
        "max_logp": 5.0,
        "max_rot_bonds": 10,
        "mutations_per_entry": 20,
    },
    "corpus": {
        "max_size": 50000,
        "priority_new_bit_weight": 10.0,
        "priority_affinity_weight": 1.0,
        "priority_reuse_penalty": 0.1,
    },
    "coverage": {
        "enabled": True,
        "mode": DEFAULT_COVERAGE_MODE,
        "map_size_kib": DEFAULT_COVERAGE_MAP_SIZE_KIB,
        "occupancy_rotate_threshold": DEFAULT_OCCUPANCY_ROTATE_THRESHOLD,
        "novelty_weights": {
            "strong": 2,
            "weak": 1,
            "none": 0,
        },
    },
    "fuzzer": {
        "workers": 4,
        "checkpoint_every": 500,
        "log_level": "INFO",
    },
}

ORACLE_FIELD_NAMES = (
    "affinity_threshold",
    "strain_threshold",
    "selectivity_ratio_min",
    "selectivity_policy",
)
_ORACLE_UNSET = object()
SELECTIVITY_POLICIES = {"fail_open", "fail_closed"}


def normalize_selectivity_policy(value: str | object = _ORACLE_UNSET) -> str:
    normalized = "fail_open" if value is _ORACLE_UNSET else str(value).strip().lower()
    if normalized not in SELECTIVITY_POLICIES:
        raise ValueError(
            "selectivity_policy must be one of "
            f"{', '.join(sorted(SELECTIVITY_POLICIES))}"
        )
    return normalized


@dataclass(frozen=True)
class BoxConfig:
    center_x: float
    center_y: float
    center_z: float
    size_x: float
    size_y: float
    size_z: float


@dataclass(frozen=True)
class PocketConfig:
    residue_ids: set[str] = field(default_factory=set)
    contact_cutoff: float = 3.5


@dataclass(frozen=True, init=False)
class OracleConfig:
    affinity_threshold: float = -9.0
    strain_threshold: float = 3.5
    selectivity_ratio_min: float = 2.0
    selectivity_policy: str = "fail_open"
    _explicit_fields: frozenset[str] = field(default_factory=frozenset, repr=False, compare=False)

    def __init__(
        self,
        affinity_threshold: float | object = _ORACLE_UNSET,
        strain_threshold: float | object = _ORACLE_UNSET,
        selectivity_ratio_min: float | object = _ORACLE_UNSET,
        selectivity_policy: str | object = _ORACLE_UNSET,
        _explicit_fields: Iterable[str] | None = None,
    ) -> None:
        explicit_fields = (
            {
                name
                for name, value in (
                    ("affinity_threshold", affinity_threshold),
                    ("strain_threshold", strain_threshold),
                    ("selectivity_ratio_min", selectivity_ratio_min),
                    ("selectivity_policy", selectivity_policy),
                )
                if value is not _ORACLE_UNSET
            }
            if _explicit_fields is None
            else {name for name in _explicit_fields if name in ORACLE_FIELD_NAMES}
        )
        object.__setattr__(
            self,
            "affinity_threshold",
            -9.0 if affinity_threshold is _ORACLE_UNSET else float(affinity_threshold),
        )
        object.__setattr__(
            self,
            "strain_threshold",
            3.5 if strain_threshold is _ORACLE_UNSET else float(strain_threshold),
        )
        object.__setattr__(
            self,
            "selectivity_ratio_min",
            2.0 if selectivity_ratio_min is _ORACLE_UNSET else float(selectivity_ratio_min),
        )
        object.__setattr__(
            self,
            "selectivity_policy",
            normalize_selectivity_policy(selectivity_policy),
        )
        object.__setattr__(self, "_explicit_fields", frozenset(explicit_fields))


@dataclass(frozen=True)
class TargetConfig:
    name: str
    receptor: str
    box: BoxConfig
    pocket: PocketConfig
    oracle: OracleConfig = field(default_factory=OracleConfig)
    offtarget_receptor: str | None = None
    offtarget_box: BoxConfig | None = None


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_global_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return copy.deepcopy(DEFAULT_GLOBAL_CONFIG)

    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to parse config.yaml. Install with `pip install pyyaml`."
        )

    with cfg_path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}

    if not isinstance(loaded, Mapping):
        raise ValueError(f"Invalid config format in {cfg_path}; expected a YAML mapping")

    merged = _deep_merge(DEFAULT_GLOBAL_CONFIG, loaded)
    merged["coverage"] = normalize_coverage_config(merged.get("coverage"))
    return merged


def normalize_coverage_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        raw: Mapping[str, Any] = {}
    elif not isinstance(value, Mapping):
        raise ValueError("coverage config must be a mapping")
    else:
        raw = value

    mode = str(raw.get("mode", DEFAULT_COVERAGE_MODE))
    map_size_kib = int(raw.get("map_size_kib", DEFAULT_COVERAGE_MAP_SIZE_KIB))
    map_size_bytes, occupancy_rotate_threshold = validate_coverage_settings(
        map_size_bytes=map_size_kib * 1024,
        occupancy_rotate_threshold=float(
            raw.get(
                "occupancy_rotate_threshold",
                DEFAULT_OCCUPANCY_ROTATE_THRESHOLD,
            )
        ),
        mode=mode,
    )
    return {
        "enabled": bool(raw.get("enabled", True)),
        "mode": mode,
        "map_size_kib": map_size_bytes // 1024,
        "occupancy_rotate_threshold": occupancy_rotate_threshold,
        "novelty_weights": normalize_novelty_weights(raw.get("novelty_weights")),
    }


def resolve_coverage_config(global_cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    if global_cfg is None:
        return normalize_coverage_config(None)
    coverage_cfg = global_cfg.get("coverage")
    if coverage_cfg is None:
        return normalize_coverage_config(None)
    if not isinstance(coverage_cfg, Mapping):
        raise ValueError("coverage config must be a mapping")
    return normalize_coverage_config(coverage_cfg)


def _to_box_config(value: BoxConfig | Mapping[str, Any]) -> BoxConfig:
    if isinstance(value, BoxConfig):
        return value
    return BoxConfig(
        center_x=float(value["center_x"]),
        center_y=float(value["center_y"]),
        center_z=float(value["center_z"]),
        size_x=float(value["size_x"]),
        size_y=float(value["size_y"]),
        size_z=float(value["size_z"]),
    )


def _to_pocket_config(value: PocketConfig | Mapping[str, Any]) -> PocketConfig:
    if isinstance(value, PocketConfig):
        return value
    residue_ids = normalize_residue_keys(value.get("residue_ids", set()))
    return PocketConfig(
        residue_ids=residue_ids,
        contact_cutoff=float(value.get("contact_cutoff", 3.5)),
    )


def _to_oracle_config(value: OracleConfig | Mapping[str, Any] | None) -> OracleConfig:
    if value is None:
        return OracleConfig()
    if isinstance(value, OracleConfig):
        return value

    explicit_fields = {
        name for name in value if name in ORACLE_FIELD_NAMES
    }
    explicit_fields.update(
        name
        for name in value.get("_explicit_fields", [])
        if name in ORACLE_FIELD_NAMES
    )
    return OracleConfig(
        affinity_threshold=float(value.get("affinity_threshold", -9.0)),
        strain_threshold=float(value.get("strain_threshold", 3.5)),
        selectivity_ratio_min=float(value.get("selectivity_ratio_min", 2.0)),
        selectivity_policy=normalize_selectivity_policy(value.get("selectivity_policy", "fail_open")),
        _explicit_fields=explicit_fields,
    )


def merge_oracle_defaults(
    target_oracle: OracleConfig | Mapping[str, Any] | None,
    global_oracle: OracleConfig | Mapping[str, Any] | None,
) -> OracleConfig:
    target_cfg = _to_oracle_config(target_oracle)
    global_cfg = _to_oracle_config(global_oracle)

    merged: dict[str, float | str] = {}
    for name in ORACLE_FIELD_NAMES:
        if name in target_cfg._explicit_fields:
            merged[name] = getattr(target_cfg, name)
        else:
            merged[name] = getattr(global_cfg, name)

    return OracleConfig(**merged, _explicit_fields=ORACLE_FIELD_NAMES)


def apply_global_defaults_to_target(
    config: TargetConfig,
    global_cfg: Mapping[str, Any] | None,
) -> TargetConfig:
    if not global_cfg:
        return config

    oracle_cfg = global_cfg.get("oracle")
    if oracle_cfg is None:
        return config

    return TargetConfig(
        name=config.name,
        receptor=config.receptor,
        box=config.box,
        pocket=config.pocket,
        oracle=merge_oracle_defaults(config.oracle, oracle_cfg),
        offtarget_receptor=config.offtarget_receptor,
        offtarget_box=config.offtarget_box,
    )


def target_from_dict(data: Mapping[str, Any]) -> TargetConfig:
    return TargetConfig(
        name=str(data["name"]),
        receptor=str(data["receptor"]),
        box=_to_box_config(data["box"]),
        pocket=_to_pocket_config(data["pocket"]),
        oracle=_to_oracle_config(data.get("oracle")),
        offtarget_receptor=(
            str(data["offtarget_receptor"]) if data.get("offtarget_receptor") else None
        ),
        offtarget_box=(
            _to_box_config(data["offtarget_box"]) if data.get("offtarget_box") else None
        ),
    )


def target_to_dict(config: TargetConfig) -> dict[str, Any]:
    payload = asdict(config)
    if isinstance(payload.get("oracle"), dict):
        payload["oracle"] = {
            name: payload["oracle"][name] for name in ORACLE_FIELD_NAMES
        }
    return payload


def _load_module_from_path(path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"biofuzz_target_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load target config from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_target_config(module: types.ModuleType, target_name: str) -> TargetConfig:
    preferred_names = [
        target_name.upper(),
        "TARGET",
        "TARGET_CONFIG",
        target_name.replace("-", "_").upper(),
    ]

    for name in preferred_names:
        value = getattr(module, name, None)
        if isinstance(value, TargetConfig):
            return value
        if isinstance(value, Mapping):
            return target_from_dict(value)

    for value in vars(module).values():
        if isinstance(value, TargetConfig):
            return value
        if isinstance(value, Mapping) and "receptor" in value and "box" in value:
            return target_from_dict(value)

    get_target = getattr(module, "get_target_config", None)
    if callable(get_target):
        value = get_target()
        if isinstance(value, TargetConfig):
            return value
        if isinstance(value, Mapping):
            return target_from_dict(value)

    raise ValueError(
        f"No TargetConfig found in {module.__file__}. "
        "Define `TARGET = TargetConfig(...)` or equivalent mapping."
    )


def load_target_config(
    target_name: str,
    targets_dir: str | Path = "targets",
) -> TargetConfig:
    config_path = Path(targets_dir) / target_name / "config.py"
    if not config_path.exists():
        raise FileNotFoundError(f"Target config not found: {config_path}")

    module = _load_module_from_path(config_path)
    config = _find_target_config(module, target_name)

    target_dir = config_path.parent

    receptor = Path(config.receptor)
    if not receptor.is_absolute():
        resolved_receptor = target_dir / receptor
        receptor = resolved_receptor if resolved_receptor.exists() or not receptor.exists() else receptor

    offtarget: Path | None = None
    if config.offtarget_receptor:
        offtarget = Path(config.offtarget_receptor)
        if not offtarget.is_absolute():
            resolved_offtarget = target_dir / offtarget
            offtarget = (
                resolved_offtarget
                if resolved_offtarget.exists() or not offtarget.exists()
                else offtarget
            )

    pocket = config.pocket
    if receptor.exists() and any(
        not is_qualified_residue_key(residue_id) for residue_id in pocket.residue_ids
    ):
        from biofuzz.protein.residues import load_residue_coordinates

        receptor_residue_ids = set(load_residue_coordinates(receptor))
        qualified_residue_ids: set[str] = set()
        for residue_id in pocket.residue_ids:
            if is_qualified_residue_key(residue_id):
                qualified_residue_ids.add(residue_id)
                continue
            matches = [
                candidate
                for candidate in receptor_residue_ids
                if residue_number(candidate) == residue_number(residue_id)
            ]
            if matches:
                qualified_residue_ids.update(sort_residue_keys(matches))
            else:
                qualified_residue_ids.add(str(residue_id))
        pocket = PocketConfig(
            residue_ids=qualified_residue_ids,
            contact_cutoff=config.pocket.contact_cutoff,
        )

    normalized = TargetConfig(
        name=config.name,
        receptor=str(receptor),
        box=config.box,
        pocket=pocket,
        oracle=config.oracle,
        offtarget_receptor=str(offtarget) if offtarget else None,
        offtarget_box=config.offtarget_box,
    )
    return normalized
