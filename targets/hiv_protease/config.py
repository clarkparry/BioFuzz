from biofuzz.docking.config import BoxConfig, OracleConfig, PocketConfig, TargetConfig

HIV_PROTEASE = TargetConfig(
    name="hiv_protease",
    receptor="protein.pdbqt",
    box=BoxConfig(
        center_x=13.073,
        center_y=22.467,
        center_z=5.557,
        size_x=20.0,
        size_y=20.0,
        size_z=20.0,
    ),
    pocket=PocketConfig(
        residue_ids={
            8,
            23,
            25,
            26,
            27,
            28,
            29,
            30,
            32,
            45,
            46,
            47,
            48,
            49,
            50,
            51,
            52,
            53,
            54,
            76,
            80,
            81,
            82,
            84,
        },
        contact_cutoff=3.5,
    ),
    oracle=OracleConfig(
        affinity_threshold=-9.0,
        strain_threshold=3.5,
        selectivity_ratio_min=2.0,
    ),
)

TARGET = HIV_PROTEASE
