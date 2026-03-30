#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import stat
import tempfile
import urllib.request
from pathlib import Path


DEFAULT_VERSION = "1.2.7"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / ".agent" / "tools" / "bin" / "vina"

ASSET_NAMES = {
    ("linux", "x86_64"): "vina_{version}_linux_x86_64",
    ("linux", "amd64"): "vina_{version}_linux_x86_64",
    ("linux", "aarch64"): "vina_{version}_linux_aarch64",
    ("darwin", "arm64"): "vina_{version}_mac_aarch64",
    ("darwin", "x86_64"): "vina_{version}_mac_x86_64",
}


def _asset_name(version: str) -> str:
    key = (platform.system().lower(), platform.machine().lower())
    template = ASSET_NAMES.get(key)
    if template is None:
        raise SystemExit(f"Unsupported platform for AutoDock Vina install: {key[0]} {key[1]}")
    return template.format(version=version)


def _download(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "BioFuzz-install-vina"})

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
    parser = argparse.ArgumentParser(description="Install the official AutoDock Vina binary locally for BioFuzz")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="AutoDock Vina release version without the leading v")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to write the executable")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing local Vina binary")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists() and not args.force:
        print(f"Already installed: {output_path}")
        return 0

    asset_name = _asset_name(version)
    url = f"https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v{version}/{asset_name}"
    _download(url, output_path)
    print(f"Installed AutoDock Vina v{version} to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
