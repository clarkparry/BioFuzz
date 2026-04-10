from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig

TARGET = TargetConfig(
    name="braf_v600e",
    receptor="protein.pdbqt",
    box=BoxConfig(
        center_x=2.643,
        center_y=-2.28,
        center_z=-19.403,
        size_x=24.305,
        size_y=18.0,
        size_z=18.0,
    ),
    pocket=PocketConfig(
        residue_ids={
            463, 471, 481, 482, 483, 505, 514, 515, 516, 527, 529, 530, 531,
            532, 583, 593, 594, 595, 596
        },
        contact_cutoff=3.5,
    ),
)
