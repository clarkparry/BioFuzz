#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open a hit pose in PyMOL")
    parser.add_argument("pose", help="Path to pose.pdbqt")
    parser.add_argument("--receptor", default=None, help="Optional receptor pdbqt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pose = Path(args.pose)
    if not pose.exists():
        raise FileNotFoundError(pose)

    cmd = ["pymol", str(pose)]
    if args.receptor:
        cmd.append(str(Path(args.receptor)))

    subprocess.run(cmd, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
