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
            759, 763, 861, 862, 863, 888, 889, 894, 895, 896, 897, 898, 903,
            904, 907, 987, 988
        },
        contact_cutoff=3.5,
    ),
)
