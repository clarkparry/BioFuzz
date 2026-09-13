#!/usr/bin/env python3
"""Install P2Rank locally for BioFuzz.

P2Rank is the ligand-free pocket detector that supplies each target's docking box
and pocket residue set (see tools/prepare_target_fixture.py). It is a Java
application: this installs the P2Rank distribution to .tools/p2rank/, but Java
(>= 11) must already be available on the system -- the distribution does not
bundle a JRE.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_VERSION = "2.4.2"
REPO_ROOT = Path(__file__).resolve().parents[1]
# prank launcher ends up at .tools/p2rank/prank -- the path prepare_target_fixture
# looks for as a repo-local fallback.
DEFAULT_OUTPUT = REPO_ROOT / ".tools" / "p2rank"


def _check_java() -> None:
    java_home = os.environ.get("JAVA_HOME")
    java_cmd = str(Path(java_home) / "bin" / "java") if java_home else shutil.which("java")
    if not java_cmd or (java_home and not Path(java_cmd).exists()):
        print(
            "WARNING: Java not found. P2Rank needs a JRE >= 11 at run time "
            "(set JAVA_HOME or put `java` on PATH). Installing anyway."
        )
        return
    try:
        result = subprocess.run([java_cmd, "-version"], capture_output=True, text=True, timeout=30)
        banner = (result.stderr or result.stdout).splitlines()
        print(f"Using Java: {banner[0].strip() if banner else java_cmd}")
    except Exception as exc:  # java present but unrunnable; not fatal for install
        print(f"WARNING: could not query java ({exc}); P2Rank needs a JRE >= 11 at run time.")


def _download(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "BioFuzz-install-p2rank"})
    with urllib.request.urlopen(request, timeout=300) as response:
        with tempfile.NamedTemporaryFile(dir=output_path.parent, delete=False) as tmp_file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                tmp_file.write(chunk)
            tmp_name = tmp_file.name
    os.replace(tmp_name, output_path)


def _extract(tarball: Path, output_dir: Path, version: str) -> None:
    with tempfile.TemporaryDirectory(dir=output_dir.parent) as staging:
        with tarfile.open(tarball) as archive:
            archive.extractall(staging, filter="data")  # 3.12+ path-traversal guard
        inner = Path(staging) / f"p2rank_{version}"
        if not inner.is_dir():
            subdirs = [p for p in Path(staging).iterdir() if p.is_dir()]
            if len(subdirs) != 1:
                raise SystemExit(f"Unexpected P2Rank archive layout: {[p.name for p in subdirs]}")
            inner = subdirs[0]
        if output_dir.exists():
            shutil.rmtree(output_dir)
        shutil.move(str(inner), str(output_dir))

    launcher = output_dir / "prank"
    if not launcher.exists():
        raise SystemExit(f"P2Rank launcher not found after extraction: {launcher}")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install P2Rank locally for BioFuzz")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="P2Rank release version")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Directory to install into")
    parser.add_argument("--force", action="store_true", help="Reinstall over an existing copy")
    args = parser.parse_args()

    output_dir = Path(args.output).expanduser().resolve()
    launcher = output_dir / "prank"
    if launcher.exists() and not args.force:
        print(f"Already installed: {launcher}")
        return 0

    _check_java()

    version = args.version.lstrip("v")
    url = f"https://github.com/rdk/p2rank/releases/download/{version}/p2rank_{version}.tar.gz"
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", dir=output_dir.parent, delete=False) as handle:
        tarball = Path(handle.name)
    try:
        print(f"Downloading P2Rank {version} ...")
        _download(url, tarball)
        _extract(tarball, output_dir, version)
    finally:
        tarball.unlink(missing_ok=True)

    print(f"Installed P2Rank {version} to {launcher}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
