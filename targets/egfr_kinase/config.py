from biofuzz.docking.config import BoxConfig, PocketConfig, TargetConfig

TARGET = TargetConfig(
    name="egfr_kinase",
    receptor="protein.pdbqt",
    box=BoxConfig(
        center_x=21.697,
        center_y=0.303,
        center_z=52.093,
        size_x=23.709,
        size_y=18.0,
        size_z=18.0,
    ),
    pocket=PocketConfig(
        residue_ids={
            694, 702, 719, 721, 738, 764, 765, 766, 767, 768, 769, 770, 771,
            772, 820, 830, 831
        },
        contact_cutoff=3.5,
    ),
)
