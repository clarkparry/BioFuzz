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
            "A:463", "A:471", "A:481", "A:482", "A:483", "A:505", "A:514",
            "A:515", "A:516", "A:527", "A:529", "A:530", "A:531", "A:532",
            "A:583", "A:593", "A:594", "A:595", "A:596"
        },
        contact_cutoff=3.5,
    ),
)
