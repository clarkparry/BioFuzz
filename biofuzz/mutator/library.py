from __future__ import annotations

# SMARTS pattern -> replacement fragment SMILES (attached at the fragment's
# first atom, in place of the matched substructure). Not an exhaustive
# implementation of every pair mentioned in mutator.md -- a representative,
# independently extensible subset demonstrating the mechanism (match ->
# delete -> reattach at one anchor bond), per the doc's own framing that
# this library "can be extended without changing any other module."
BIOISOSTERE_PAIRS = [
    ("[CX3](=O)[OX2H1]", "c1nnn[nH]1"),  # -COOH -> tetrazole
    ("[OX2H1]", "F"),  # -OH -> -F
    ("[OX2H1]", "N"),  # -OH -> -NH2
    ("[CH2X4]", "O"),  # -CH2- linker -> -O-
    ("[CH2X4]", "N"),  # -CH2- linker -> -NH-
]
