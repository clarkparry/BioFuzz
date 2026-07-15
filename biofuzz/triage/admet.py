from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import Descriptors

from biofuzz.triage.record import TriageRecord, TriageStageResult


class ADMETStage:
    name = "admet"

    def analyze(self, record: TriageRecord, target_config: dict, **kwargs) -> TriageStageResult:
        mol = Chem.MolFromSmiles(record.smiles)
        if mol is None:
            return TriageStageResult(fields={}, filter_failed="invalid_smiles")

        mw = Descriptors.MolWt(mol)
        logp = Descriptors.MolLogP(mol)
        hbd = Descriptors.NumHDonors(mol)
        hba = Descriptors.NumHAcceptors(mol)
        tpsa = Descriptors.TPSA(mol)
        rot_bonds = Descriptors.NumRotatableBonds(mol)
        heavy_atoms = mol.GetNumHeavyAtoms()

        flags = []
        if mw > 500:
            flags.append("lipinski_mw_violation")
        if hbd > 5:
            flags.append("lipinski_hbd_violation")
        if hba > 10:
            flags.append("lipinski_hba_violation")
        if logp > 5:
            flags.append("lipinski_logp_violation")
        if tpsa > 140:
            flags.append("tpsa_violation")
        if rot_bonds > 10:
            flags.append("rot_bonds_violation")

        return TriageStageResult(
            fields=dict(
                molecular_weight=mw,
                logp=logp,
                hbd=hbd,
                hba=hba,
                tpsa=tpsa,
                rot_bonds=rot_bonds,
                heavy_atom_count=heavy_atoms,
            ),
            flags=flags,
        )
