from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig

TARGET = TargetConfig(
    name="sars_cov2_mpro",
    receptor="protein.pdbqt",
    box=BoxConfig(
        center_x=-10.685,
        center_y=39.898,
        center_z=-17.503,
        size_x=18.0,
        size_y=18.755,
        size_z=18.0,
    ),
    pocket=PocketConfig(
        residue_ids={
            "A:41", "A:49", "A:54", "A:140", "A:141", "A:142", "A:143",
            "A:144", "A:145", "A:163", "A:164", "A:165", "A:166", "A:167",
            "A:168", "A:172", "A:187", "A:188", "A:189", "A:190", "A:192"
        },
        contact_cutoff=3.5,
    ),
)
