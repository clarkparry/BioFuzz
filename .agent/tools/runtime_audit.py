#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biofuzz.docking import runner


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str
    available: bool
    detail: str | None = None


PYTHON_MODULES = (
    ("yaml", "PyYAML"),
    ("rdkit", "RDKit"),
    ("meeko", "Meeko"),
)

DOCKING_BINARIES = ("gnina", "vina", "quickvina2", "quickvina-w")


def _module_check(module_name: str, label: str) -> CheckResult:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        return CheckResult(
            name=label,
            kind="python-module",
            available=False,
            detail=f"{module_name}: {exc}",
        )

    return CheckResult(name=label, kind="python-module", available=True, detail=module_name)


def _binary_check(binary_name: str) -> CheckResult:
    resolved = runner._resolve_candidate_binary(binary_name)
    return CheckResult(
        name=binary_name,
        kind="binary",
        available=resolved is not None,
        detail=resolved,
    )


def collect_runtime_status() -> dict[str, object]:
    modules = [_module_check(module_name, label) for module_name, label in PYTHON_MODULES]
    binaries = [_binary_check(binary_name) for binary_name in DOCKING_BINARIES]

    module_status = {item.name: item.available for item in modules}
    live_run_ready = (
        module_status.get("RDKit", False)
        and module_status.get("Meeko", False)
        and any(item.available for item in binaries)
    )

    return {
        "live_run_ready": live_run_ready,
        "python_modules": [asdict(item) for item in modules],
        "docking_binaries": [asdict(item) for item in binaries],
    }


def _print_human(status: dict[str, object]) -> None:
    print(f"live_run_ready: {status['live_run_ready']}")
    print("python_modules:")
    for item in status["python_modules"]:
        marker = "ok" if item["available"] else "missing"
        detail = f" ({item['detail']})" if item.get("detail") else ""
        print(f"  - {item['name']}: {marker}{detail}")
    print("docking_binaries:")
    for item in status["docking_binaries"]:
        marker = item["detail"] if item["available"] else "missing"
        print(f"  - {item['name']}: {marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit BioFuzz runtime dependencies")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    status = collect_runtime_status()
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        _print_human(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
