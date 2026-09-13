#!/usr/bin/env python3
"""Check that this machine can actually run a BioFuzz campaign.

Reports on the three things a campaign needs: the Python dependencies, the
docking engine, and at least one prepared target. Exits non-zero if anything
required is missing, so it can gate a setup script or CI job.

    python tools/check_environment.py
    python tools/check_environment.py --json
"""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# (import name, human label, required for a campaign)
PYTHON_MODULES = (
    ("yaml", "PyYAML", True),
    ("rdkit", "RDKit", True),
    ("meeko", "Meeko", True),
    ("gemmi", "gemmi", False),
    ("scipy", "SciPy", False),
)

MIN_PYTHON = (3, 12)


@dataclass(frozen=True)
class Check:
    name: str
    kind: str
    ok: bool
    required: bool
    detail: str | None = None


def _python_check() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = sys.version_info[:2] >= MIN_PYTHON
    wanted = ".".join(str(part) for part in MIN_PYTHON)
    return Check(
        name=f"Python >= {wanted}",
        kind="interpreter",
        ok=ok,
        required=True,
        detail=f"{version} at {sys.executable}",
    )


def _module_check(module_name: str, label: str, required: bool) -> Check:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return Check(label, "python-module", False, required, f"{module_name}: {exc}")
    version = getattr(module, "__version__", None)
    return Check(label, "python-module", True, required, version or module_name)


def _engine_check() -> Check:
    """Resolve the docking engine the same way the fuzzer does, then run it."""
    try:
        from biofuzz.docker import get_backend

        backend = get_backend("gnina")
    except Exception as exc:
        return Check("gnina", "engine", False, True, f"backend unavailable: {exc}")

    issues = backend.runtime_issues()
    if issues:
        return Check("gnina", "engine", False, True, "; ".join(issues))

    path = backend._resolve_binary()
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=120)
    except Exception as exc:
        return Check("gnina", "engine", False, True, f"{path}: {exc}")
    if proc.returncode != 0:
        # Most often a missing CUDA runtime library: the prebuilt binary is
        # CUDA-linked even when it runs CPU inference.
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return Check(
            "gnina", "engine", False, True,
            f"{path} exited {proc.returncode}: {detail[-1] if detail else 'no output'}",
        )
    banner = (proc.stdout or "").strip().splitlines()
    return Check("gnina", "engine", True, True, f"{banner[0] if banner else path} ({path})")


def _p2rank_check() -> Check:
    """Only needed to prepare new targets, not to run a campaign."""
    candidates = [shutil.which("prank"), shutil.which("p2rank")]
    local = REPO_ROOT / ".tools" / "p2rank" / "prank"
    if local.exists():
        candidates.append(str(local))
    found = next((c for c in candidates if c), None)
    if not found:
        return Check(
            "P2Rank", "tool", False, False,
            "not found; needed only by tools/prepare_target_fixture.py",
        )
    java = shutil.which("java")
    if not java:
        return Check("P2Rank", "tool", False, False, f"{found} found but no java on PATH")
    return Check("P2Rank", "tool", True, False, found)


def _targets_check() -> list[Check]:
    """A target is usable once its receptor PDBQT has been built."""
    targets_root = REPO_ROOT / "targets"
    checks: list[Check] = []
    if not targets_root.is_dir():
        return [Check("targets/", "target", False, True, "directory missing")]

    names = sorted(p.name for p in targets_root.iterdir() if (p / "config.yaml").exists())
    for name in names:
        receptor = targets_root / name / "protein.pdbqt"
        if receptor.exists():
            size_kb = receptor.stat().st_size // 1024
            checks.append(Check(name, "target", True, False, f"receptor {size_kb} KiB"))
        else:
            checks.append(
                Check(name, "target", False, False, "protein.pdbqt not built")
            )
    if not names:
        checks.append(Check("targets/", "target", False, True, "no target configs found"))
    return checks


def collect() -> dict:
    checks = [_python_check()]
    checks += [_module_check(m, label, req) for m, label, req in PYTHON_MODULES]
    checks.append(_engine_check())
    checks.append(_p2rank_check())
    target_checks = _targets_check()

    required_ok = all(c.ok for c in checks if c.required)
    any_target = any(c.ok for c in target_checks if c.kind == "target")

    return {
        "ready_to_fuzz": required_ok and any_target,
        "checks": [asdict(c) for c in checks],
        "targets": [asdict(c) for c in target_checks],
    }


def _print_human(status: dict) -> None:
    def line(item):
        mark = "ok  " if item["ok"] else ("MISS" if item["required"] else "--  ")
        detail = f"  {item['detail']}" if item.get("detail") else ""
        print(f"  [{mark}] {item['name']}{detail}")

    print("Dependencies")
    for item in status["checks"]:
        line(item)
    print("\nTargets")
    for item in status["targets"]:
        line(item)

    print()
    if status["ready_to_fuzz"]:
        print("Ready to fuzz. Try: ./biofuzz-fuzz --target hiv_protease --max-iterations 1")
    else:
        print("Not ready. Items marked MISS are required; see the Setup section of README.md.")
        print("Targets showing 'protein.pdbqt not built' need:")
        print("  python tools/prepare_target_fixture.py <name>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    status = collect()
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        _print_human(status)
    return 0 if status["ready_to_fuzz"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
