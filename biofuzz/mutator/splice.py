from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import rdFMCS
from rdkit.Chem.Scaffolds import MurckoScaffold

from biofuzz.mutator.ops import attach_fragment, open_positions


def scaffold_splice(mol, donor_mol, rng):
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    donor_scaffold = MurckoScaffold.GetScaffoldForMol(donor_mol)
    if scaffold is None or scaffold.GetNumAtoms() == 0:
        return None
    if donor_scaffold is None:
        return None

    try:
        r_groups = Chem.DeleteSubstructs(donor_mol, donor_scaffold)
    except Exception:
        return None
    if r_groups.GetNumAtoms() == 0:
        return None

    positions = open_positions(scaffold)
    if not positions:
        return None

    atom_idx = rng.choice(positions)
    frag_smiles = Chem.MolToSmiles(r_groups).split(".")[0]
    return attach_fragment(scaffold, atom_idx, frag_smiles)


def fragment_graft(mol, donor_mol, rng):
    mcs = rdFMCS.FindMCS([mol, donor_mol], timeout=2)
    if mcs.numAtoms < 3:
        return None

    patt = Chem.MolFromSmarts(mcs.smartsString)
    if patt is None or not mol.HasSubstructMatch(patt):
        return None

    try:
        non_shared = Chem.DeleteSubstructs(donor_mol, patt)
    except Exception:
        return None
    if non_shared.GetNumAtoms() == 0:
        return None

    match = mol.GetSubstructMatch(patt)
    if not match:
        return None

    anchor_idx = match[0]
    frag_smiles = Chem.MolToSmiles(non_shared).split(".")[0]
    return attach_fragment(mol, anchor_idx, frag_smiles)
