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
            41, 49, 54, 140, 141, 142, 143, 144, 145, 163, 164, 165, 166, 167,
            168, 172, 187, 188, 189, 190, 192
        },
        contact_cutoff=3.5,
    ),
)
