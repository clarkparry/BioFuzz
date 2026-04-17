from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig

TARGET = TargetConfig(
    name="parp1",
    receptor="protein.pdbqt",
    box=BoxConfig(
        center_x=2.314,
        center_y=63.631,
        center_z=188.326,
        size_x=18.222,
        size_y=18.0,
        size_z=18.0,
    ),
    pocket=PocketConfig(
        residue_ids={
            "A:759", "A:763", "A:861", "A:862", "A:863", "A:888", "A:889",
            "A:894", "A:895", "A:896", "A:897", "A:898", "A:903", "A:904",
            "A:907", "A:987", "A:988"
        },
        contact_cutoff=3.5,
    ),
)
