from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import Crippen
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams

from biofuzz.mutator.alerts import unstable_motifs
from biofuzz.triage.record import TriageRecord, TriageStageResult

_PAINS_PARAMS = FilterCatalogParams()
_PAINS_PARAMS.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
_PAINS_CATALOG = FilterCatalog(_PAINS_PARAMS)

# Reactive-group screen: not exhaustive, covers the doc's named examples
# (aldehydes, acyl halides, Michael acceptors) plus a couple of other
# classically flagged reactive motifs (epoxides, alkyl halides).
_REACTIVE_PATTERNS = {
    "aldehyde": "[CX3H1](=O)[#6]",
    "acyl_halide": "[CX3](=[OX1])[F,Cl,Br,I]",
    "michael_acceptor": "[CX3]=[CX3][CX3]=[OX1]",
    "epoxide": "[OX2r3]1[#6r3][#6r3]1",
    "alkyl_halide": "[CX4][F,Cl,Br,I]",
}
_REACTIVE_MOLS = {name: Chem.MolFromSmarts(smarts) for name, smarts in _REACTIVE_PATTERNS.items()}


def _is_aggregator_like(mol: Chem.Mol) -> bool:
    # Crude heuristic per the doc's examples ("cationic amphiphiles, certain
    # polyaromatics") -- not a validated aggregator predictor.
    formal_charge = Chem.GetFormalCharge(mol)
    logp = Crippen.MolLogP(mol)
    if formal_charge > 0 and logp > 3.0:
        return True

    ring_info = mol.GetRingInfo()
    aromatic_rings = sum(
        1 for ring in ring_info.AtomRings() if all(mol.GetAtomWithIdx(i).GetIsAromatic() for i in ring)
    )
    return aromatic_rings >= 4


class ChemistryFlagsStage:
    name = "chemistry_flags"

    def analyze(self, record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult:
        mol = Chem.MolFromSmiles(record.smiles)
        if mol is None:
            return TriageStageResult(fields={}, filter_failed="invalid_smiles")

        flags = []
        if _PAINS_CATALOG.HasMatch(mol):
            flags.append("pains_match")

        # Chemically implausible motifs. The fuzzing loop rejects these outright
        # now (biofuzz/mutator/alerts.py), so this should only ever fire on
        # findings from a campaign that predates that gate -- which is exactly
        # when a reader most needs to be told.
        for motif in unstable_motifs(mol):
            flags.append(f"unstable_motif:{motif}")

        for name, patt in _REACTIVE_MOLS.items():
            if patt is not None and mol.HasSubstructMatch(patt):
                flags.append(f"reactive_group:{name}")

        if _is_aggregator_like(mol):
            flags.append("possible_aggregator")

        return TriageStageResult(fields={}, flags=flags)
