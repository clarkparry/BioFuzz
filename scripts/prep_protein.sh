#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <input.pdb> <output.pdbqt>"
  echo "Example: $0 protein.pdb targets/hiv_protease/protein.pdbqt"
  exit 1
fi

INPUT_PDB="$1"
OUTPUT_PDBQT="$2"

if command -v prepare_receptor >/dev/null 2>&1; then
  prepare_receptor -r "$INPUT_PDB" -o "$OUTPUT_PDBQT"
else
  echo "prepare_receptor not found in PATH. Install ADFR suite first." >&2
  exit 2
fi
