#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import stat
import tempfile
import urllib.request
from pathlib import Path


DEFAULT_VERSION = "1.3.2"
DEFAULT_VARIANT = "compat"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / ".tools" / "bin" / "gnina"

VARIANT_SUFFIX = {
    "compat": "",
    "cuda12.8": ".cuda12.8",
}


def _asset_name(version: str, variant: str) -> str:
    normalized_variant = variant.lower()
    suffix = VARIANT_SUFFIX.get(normalized_variant)
    if suffix is None:
        supported = ", ".join(sorted(VARIANT_SUFFIX))
        raise SystemExit(f"Unsupported GNINA variant '{variant}'. Supported values: {supported}")
    return f"gnina.{version}{suffix}"


def _validate_platform() -> None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system != "linux" or machine not in {"x86_64", "amd64"}:
        raise SystemExit(f"Unsupported platform for GNINA install helper: {system} {machine}")


def _download(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "BioFuzz-install-gnina"})

    with urllib.request.urlopen(request, timeout=120) as response:
        with tempfile.NamedTemporaryFile(dir=output_path.parent, delete=False) as tmp_file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                tmp_file.write(chunk)
            tmp_name = tmp_file.name

    os.replace(tmp_name, output_path)
    output_path.chmod(output_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the GNINA binary locally for BioFuzz")
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help="GNINA release version without the leading v",
    )
    parser.add_argument(
        "--variant",
        default=DEFAULT_VARIANT,
        choices=sorted(VARIANT_SUFFIX),
        help="Release asset variant (`compat` is the default binary from GNINA releases)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to write the executable",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing local GNINA binary",
    )
    args = parser.parse_args()

    _validate_platform()

    version = args.version.lstrip("v")
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists() and not args.force:
        print(f"Already installed: {output_path}")
        return 0

    asset_name = _asset_name(version, args.variant)
    url = f"https://github.com/gnina/gnina/releases/download/v{version}/{asset_name}"
    _download(url, output_path)
    print(f"Installed GNINA v{version} ({args.variant}) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
