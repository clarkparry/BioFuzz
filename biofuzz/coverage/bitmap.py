from __future__ import annotations

import base64
from dataclasses import dataclass

from biofuzz.coverage.fingerprint import build_fingerprint
from biofuzz.coverage.hashing import splitmix64
from biofuzz.storage.checkpoint import (
    CheckpointVersionError,
    load_checkpoint,
    save_checkpoint,
)

CHECKPOINT_VERSION = 3

# (max cumulative hits inclusive, bucket value) per the AFL++-style byte model.
_BUCKET_THRESHOLDS = [(0, 0), (1, 1), (2, 2), (3, 3), (7, 4), (15, 5), (31, 6)]


def _bucket_for_count(count: int) -> int:
    for max_count, bucket in _BUCKET_THRESHOLDS:
        if count <= max_count:
            return bucket
    return 7


@dataclass
class CoverageObservation:
    fingerprint: frozenset
    fingerprint_mask: int
    hash_slot: int
    novelty_class: str
    novelty_score: int
    bitmap_slot_byte: int
    bitmap_occupancy: float
    epoch: int
    rotated: bool


class CoverageMap:
    def __init__(
        self,
        pocket_residue_ids,
        map_size_bytes: int = 262144,
        occupancy_rotate_threshold: float = 0.55,
        interaction_types_enabled: bool = False,
        novelty_weights: dict | None = None,
        contact_cutoff: float = 3.5,
    ):
        if map_size_bytes & (map_size_bytes - 1) != 0:
            raise ValueError("map_size_bytes must be a power of two")

        self.pocket_residue_ids = sorted(pocket_residue_ids)
        self.map_size_bytes = map_size_bytes
        self.occupancy_rotate_threshold = occupancy_rotate_threshold
        self.interaction_types_enabled = interaction_types_enabled
        self.novelty_weights = novelty_weights or {"strong": 2, "weak": 1, "none": 0}
        self.contact_cutoff = contact_cutoff

        self.residue_to_bit_index = self._build_bit_index()

        self.epoch = 0
        self.current = bytearray(map_size_bytes)
        self.previous = bytearray(map_size_bytes)
        self._current_counts = [0] * map_size_bytes
        self.current_nonzero_count = 0
        self.pioneers: dict[int, str] = {}
        self.novelty_counts = {"strong": 0, "weak": 0, "none": 0}

    def _build_bit_index(self) -> dict[str, int]:
        mapping: dict[str, int] = {}
        slots_per_residue = 4 if self.interaction_types_enabled else 1
        for i, rid in enumerate(self.pocket_residue_ids):
            base = i * slots_per_residue
            mapping[rid] = base
            if self.interaction_types_enabled:
                mapping[f"{rid}:hbond_donor"] = base + 1
                mapping[f"{rid}:hbond_acceptor"] = base + 2
                mapping[f"{rid}:hydrophobic"] = base + 3
        return mapping

    def _fingerprint_mask(self, fingerprint) -> int:
        mask = 0
        for item in fingerprint:
            bit = self.residue_to_bit_index.get(item)
            if bit is not None:
                mask |= 1 << bit
        return mask

    def observe(
        self, pose_atoms_or_fingerprint, protein_residues=None, smiles: str | None = None
    ) -> CoverageObservation:
        if protein_residues is None and isinstance(pose_atoms_or_fingerprint, (frozenset, set)):
            fingerprint = frozenset(pose_atoms_or_fingerprint)
        else:
            fingerprint = build_fingerprint(
                pose_atoms_or_fingerprint,
                protein_residues,
                contact_cutoff=self.contact_cutoff,
                interaction_types_enabled=self.interaction_types_enabled,
            )

        mask = self._fingerprint_mask(fingerprint)
        slot = splitmix64(mask) & (self.map_size_bytes - 1)

        in_current = self.current[slot] != 0
        in_previous = self.previous[slot] != 0

        if not in_current and not in_previous:
            novelty_class = "strong"
        elif not in_current and in_previous:
            novelty_class = "weak"
        else:
            novelty_class = "none"

        self.novelty_counts[novelty_class] += 1

        if self.current[slot] == 0:
            self.current_nonzero_count += 1
        self._current_counts[slot] += 1
        self.current[slot] = _bucket_for_count(self._current_counts[slot])

        if novelty_class in ("strong", "weak"):
            if smiles is not None and slot not in self.pioneers:
                self.pioneers[slot] = smiles

        rotated = False
        if self.bitmap_occupancy() >= self.occupancy_rotate_threshold:
            self._rotate_epoch()
            rotated = True

        return CoverageObservation(
            fingerprint=fingerprint,
            fingerprint_mask=mask,
            hash_slot=slot,
            novelty_class=novelty_class,
            novelty_score=self.novelty_weights.get(novelty_class, 0),
            bitmap_slot_byte=self.current[slot],
            bitmap_occupancy=self.bitmap_occupancy(),
            epoch=self.epoch,
            rotated=rotated,
        )

    def _rotate_epoch(self) -> None:
        self.previous = self.current
        self.current = bytearray(self.map_size_bytes)
        self._current_counts = [0] * self.map_size_bytes
        self.current_nonzero_count = 0
        self.epoch += 1

    def bitmap_occupancy(self) -> float:
        return self.current_nonzero_count / self.map_size_bytes

    def pioneer_for(self, slot: int) -> str | None:
        return self.pioneers.get(slot)

    @property
    def strong_novelty_count(self) -> int:
        return self.novelty_counts["strong"]

    def stats(self) -> dict:
        return {
            "epoch": self.epoch,
            "bitmap_occupancy": self.bitmap_occupancy(),
            "novelty_counts": dict(self.novelty_counts),
            "current_nonzero_count": self.current_nonzero_count,
        }

    def save(self, path) -> None:
        data = {
            "version": CHECKPOINT_VERSION,
            "map_size_bytes": self.map_size_bytes,
            "occupancy_rotate_threshold": self.occupancy_rotate_threshold,
            "interaction_types_enabled": self.interaction_types_enabled,
            "epoch": self.epoch,
            "current_nonzero_count": self.current_nonzero_count,
            "current_map_b64": base64.b64encode(bytes(self.current)).decode("ascii"),
            "previous_map_b64": base64.b64encode(bytes(self.previous)).decode("ascii"),
            "pocket_residue_ids": self.pocket_residue_ids,
            "residue_to_bit_index": self.residue_to_bit_index,
            "novelty_counts": self.novelty_counts,
            "pioneers": {str(k): v for k, v in self.pioneers.items()},
        }
        save_checkpoint(path, data)

    def load(self, path) -> None:
        data = load_checkpoint(path)
        if data.get("version") != CHECKPOINT_VERSION:
            raise CheckpointVersionError(
                f"coverage checkpoint version {data.get('version')} != expected {CHECKPOINT_VERSION}"
            )

        self.map_size_bytes = data["map_size_bytes"]
        self.occupancy_rotate_threshold = data["occupancy_rotate_threshold"]
        self.interaction_types_enabled = data["interaction_types_enabled"]
        self.epoch = data["epoch"]
        self.current_nonzero_count = data["current_nonzero_count"]
        self.current = bytearray(base64.b64decode(data["current_map_b64"]))
        self.previous = bytearray(base64.b64decode(data["previous_map_b64"]))
        self.pocket_residue_ids = data["pocket_residue_ids"]
        self.residue_to_bit_index = data["residue_to_bit_index"]
        self.novelty_counts = data["novelty_counts"]
        self.pioneers = {int(k): v for k, v in data["pioneers"].items()}
        self._current_counts = [self.current[i] for i in range(self.map_size_bytes)]
