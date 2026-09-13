"""Structural alerts for chemically implausible mutants.

The drug-likeness filter (MW / logP / HBD / HBA / rotatable bonds) says nothing
about whether a molecule could exist. Graph mutations readily produce motifs no
chemist would synthesise: atom_scan walking an ethoxy chain one atom at a time
yields aryl-O-N(H)-O and aryl-O-CH2-O-H, a hydroxylamine ether and a hemiacetal.
Such molecules pass every property bound, dock well, and would be saved as
findings, so they have to be rejected on structure instead.

This catalog is deliberately narrow: it targets motifs that are *unstable or
implausible*, not motifs that are merely unattractive. General medchem filters
(BRENK, PAINS) were rejected for the in-loop gate because they are far too
aggressive here -- BRENK flags aspirin (phenol ester) and every aniline, both of
which appear in approved drugs. PAINS/BRENK still run in triage, where flagging
is advisory rather than a hard reject.

Validated against `seeds/approved_drugs.smi`: 0 of 250 approved drugs are
flagged, while artemisinin (ring endoperoxide), paroxetine (benzodioxole),
aspirin (phenol ester) and vorinostat (hydroxamic acid) all pass -- each of
which a naive version of these patterns would wrongly reject. Ring membership
and acylation exclusions are what buy that.
"""

from __future__ import annotations

from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")

# Matches N/O attached to a carbonyl-like centre: amides, esters, hydroxamic
# acids, sulfonamides, phosphates. These stabilise the adjacent heteroatom, so
# an N-O or N-C-N that looks alarming in isolation is fine here.
_ACYLATED = "$([#7,#8][C,S,P]=[O,S,N])"

UNSTABLE_PATTERNS: dict[str, str] = {
    # O-O outside a ring. Ring peroxides are excluded so artemisinin and other
    # endoperoxide natural products survive.
    "acyclic_peroxide": (
        "[OX2;!R;!$([OX2][C,S,P]=[O,S,N])][OX2;!R;!$([OX2][C,S,P]=[O,S,N])]"
    ),
    # N-O single bonds: hydroxylamine ethers and the like. Excludes hydroxamic
    # acids / N-oxides / nitro groups, and O bound to an aromatic ring.
    "n_o_single_bond": (
        f"[OX2;!R;!{_ACYLATED};!$([OX2][a])]"
        f"[NX3;!R;!{_ACYLATED};!$([NX3](=O));!$([NX3+][O-])]"
    ),
    # O-C-O on an acyclic carbon: acetals and hemiacetals, which hydrolyse.
    # The !R on the central carbon is what spares methylenedioxy/benzodioxole.
    "acyclic_acetal": (
        "[OX2;!$(O[C,S,P]=[O,S,N])][CX4;H1,H2;!R][OX2;!$(O[C,S,P]=[O,S,N])]"
    ),
    "acyclic_n_o_acetal": (
        f"[OX2;!$(O[C,S,P]=[O,S,N])][CX4;H1,H2;!R][NX3;!{_ACYLATED}]"
    ),
    "acyclic_aminal": f"[NX3;!{_ACYLATED}][CX4;H1,H2;!R][NX3;!{_ACYLATED}]",
    "acyclic_thioacetal": (
        "[SX2;!$(S[C,S,P]=[O,S,N])][CX4;H1,H2;!R][SX2;!$(S[C,S,P]=[O,S,N])]"
    ),
    # Geminal diol: the hydrate of a ketone, not an isolable species.
    "gem_diol": "[CX4]([OX2H])[OX2H]",
    # Heteroatom-halogen bonds and acyl halides: violently reactive.
    "acyl_halide": "[CX3](=[OX1])[F,Cl,Br,I]",
    "o_halide": "[OX2][F,Cl,Br,I]",
    "n_halide": "[NX3][F,Cl,Br,I]",
    "s_halide": "[SX2][F,Cl,Br,I]",
    "allene": "[CX2](=C)=C",
    "perhalo_carbon": "[CX4]([F,Cl,Br,I])([F,Cl,Br,I])([F,Cl,Br,I])[F,Cl,Br,I]",
}

_COMPILED: dict[str, Chem.Mol] = {}
for _name, _smarts in UNSTABLE_PATTERNS.items():
    _query = Chem.MolFromSmarts(_smarts)
    if _query is not None:
        _COMPILED[_name] = _query


def unstable_motifs(mol: Chem.Mol) -> list[str]:
    """Names of every unstable motif present in `mol`."""
    if mol is None:
        return ["invalid_molecule"]
    return [name for name, query in _COMPILED.items() if mol.HasSubstructMatch(query)]


def is_chemically_plausible(mol: Chem.Mol) -> bool:
    """True when `mol` carries no unstable motif."""
    if mol is None:
        return False
    return not any(mol.HasSubstructMatch(query) for query in _COMPILED.values())


def unstable_motifs_for_smiles(smiles: str) -> list[str]:
    return unstable_motifs(Chem.MolFromSmiles(smiles))
