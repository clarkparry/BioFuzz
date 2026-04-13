from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import base64
import json
from typing import Mapping

COVERAGE_CHECKPOINT_VERSION = 2
DEFAULT_COVERAGE_MODE = "hashed_fingerprint"
DEFAULT_COVERAGE_MAP_SIZE_KIB = 256
DEFAULT_COVERAGE_MAP_SIZE_BYTES = DEFAULT_COVERAGE_MAP_SIZE_KIB * 1024
MIN_COVERAGE_MAP_SIZE_BYTES = 1024
DEFAULT_OCCUPANCY_ROTATE_THRESHOLD = 0.55
DEFAULT_NOVELTY_WEIGHTS = {
    "strong": 2,
    "weak": 1,
    "none": 0,
}
UINT64_MASK = (1 << 64) - 1
_SPLITMIX_GAMMA = 0x9E3779B97F4A7C15


def normalize_novelty_weights(weights: Mapping[str, int] | None = None) -> dict[str, int]:
    merged = dict(DEFAULT_NOVELTY_WEIGHTS)
    if weights is not None:
        for key, value in weights.items():
            if key not in merged:
                raise ValueError(f"Unknown novelty weight key: {key}")
            normalized = int(value)
            if normalized < 0:
                raise ValueError(f"Novelty weight {key} must be >= 0")
            merged[key] = normalized
    return merged


def validate_coverage_settings(
    *,
    map_size_bytes: int,
    occupancy_rotate_threshold: float,
    mode: str = DEFAULT_COVERAGE_MODE,
) -> tuple[int, float]:
    normalized_mode = str(mode)
    if normalized_mode != DEFAULT_COVERAGE_MODE:
        raise ValueError(f"Unsupported coverage mode: {normalized_mode}")

    normalized_map_size = int(map_size_bytes)
    if normalized_map_size < MIN_COVERAGE_MAP_SIZE_BYTES:
        raise ValueError(
            f"coverage.map_size_bytes must be >= {MIN_COVERAGE_MAP_SIZE_BYTES}"
        )
    if normalized_map_size & (normalized_map_size - 1):
        raise ValueError("coverage.map_size_bytes must be a power of two")

    normalized_threshold = float(occupancy_rotate_threshold)
    if not 0.1 <= normalized_threshold <= 0.95:
        raise ValueError(
            "coverage.occupancy_rotate_threshold must be between 0.1 and 0.95"
        )

    return normalized_map_size, normalized_threshold


@dataclass(frozen=True)
class CoverageObservation:
    fingerprint: frozenset[int]
    fingerprint_mask: int
    hash_index: int
    novelty_class: str
    novelty_score: int
    bitmap_occupancy: float
    epoch: int
    rotated: bool = False


class CoverageMap:
    def __init__(
        self,
        pocket_residue_ids: set[int] | list[int] | tuple[int, ...],
        *,
        map_size_bytes: int = DEFAULT_COVERAGE_MAP_SIZE_BYTES,
        occupancy_rotate_threshold: float = DEFAULT_OCCUPANCY_ROTATE_THRESHOLD,
        mode: str = DEFAULT_COVERAGE_MODE,
        novelty_weights: Mapping[str, int] | None = None,
        enabled: bool = True,
    ):
        validated_map_size, validated_threshold = validate_coverage_settings(
            map_size_bytes=map_size_bytes,
            occupancy_rotate_threshold=occupancy_rotate_threshold,
            mode=mode,
        )
        self.mode = str(mode)
        self.enabled = bool(enabled)
        self.map_size_bytes = validated_map_size
        self.occupancy_rotate_threshold = validated_threshold
        self.novelty_weights = normalize_novelty_weights(novelty_weights)
        self._set_pocket_residue_ids(pocket_residue_ids)
        self.current_map = bytearray(self.map_size_bytes)
        self.previous_map = bytearray(self.map_size_bytes)
        self.current_nonzero_count = 0
        self.epoch = 0
        self.strong_novelty_count = 0
        self.weak_novelty_count = 0
        self.none_novelty_count = 0

    def _set_pocket_residue_ids(
        self,
        pocket_residue_ids: set[int] | list[int] | tuple[int, ...],
    ) -> None:
        self.pocket_residue_ids: set[int] = {int(rid) for rid in pocket_residue_ids}
        self._sorted_pocket_residue_ids = tuple(sorted(self.pocket_residue_ids))
        self.residue_to_bit_index = {
            residue_id: idx for idx, residue_id in enumerate(self._sorted_pocket_residue_ids)
        }

    def _normalize_fingerprint(self, fingerprint: frozenset[int]) -> frozenset[int]:
        return frozenset(
            int(rid) for rid in fingerprint if int(rid) in self.pocket_residue_ids
        )

    def fingerprint_to_mask(self, fingerprint: frozenset[int]) -> tuple[frozenset[int], int]:
        valid_fingerprint = self._normalize_fingerprint(fingerprint)
        mask = 0
        for residue_id in valid_fingerprint:
            mask |= 1 << self.residue_to_bit_index[residue_id]
        return valid_fingerprint, mask

    @staticmethod
    def _mix64(value: int) -> int:
        mixed = value & UINT64_MASK
        mixed ^= mixed >> 30
        mixed = (mixed * 0xBF58476D1CE4E5B9) & UINT64_MASK
        mixed ^= mixed >> 27
        mixed = (mixed * 0x94D049BB133111EB) & UINT64_MASK
        mixed ^= mixed >> 31
        return mixed & UINT64_MASK

    def hash_mask(self, fingerprint_mask: int) -> int:
        if not self.enabled:
            return -1
        if fingerprint_mask == 0:
            return self._mix64(0) & (self.map_size_bytes - 1)

        mixed = self._mix64(len(self._sorted_pocket_residue_ids) + _SPLITMIX_GAMMA)
        chunk_index = 0
        remaining = fingerprint_mask
        while remaining:
            chunk = remaining & UINT64_MASK
            mixed = self._mix64(
                (mixed + chunk + ((chunk_index + 1) * _SPLITMIX_GAMMA)) & UINT64_MASK
            )
            remaining >>= 64
            chunk_index += 1
        return mixed & (self.map_size_bytes - 1)

    def bitmap_occupancy(self) -> float:
        if not self.enabled or self.map_size_bytes == 0:
            return 0.0
        return self.current_nonzero_count / self.map_size_bytes

    def novelty_counts(self) -> dict[str, int]:
        return {
            "strong": self.strong_novelty_count,
            "weak": self.weak_novelty_count,
            "none": self.none_novelty_count,
        }

    def stats(self) -> dict[str, float | int | str]:
        counts = self.novelty_counts()
        return {
            "coverage_mode": self.mode,
            "coverage_bitmap_occupancy": self.bitmap_occupancy(),
            "coverage_epoch": self.epoch,
            "novelty_strong_count": counts["strong"],
            "novelty_weak_count": counts["weak"],
            "novelty_none_count": counts["none"],
        }

    def _record_novelty(self, novelty_class: str) -> None:
        if novelty_class == "strong":
            self.strong_novelty_count += 1
        elif novelty_class == "weak":
            self.weak_novelty_count += 1
        else:
            self.none_novelty_count += 1

    def _rotate_epoch(self) -> None:
        self.previous_map, self.current_map = self.current_map, self.previous_map
        self.current_map[:] = b"\x00" * self.map_size_bytes
        self.current_nonzero_count = 0
        self.epoch += 1

    def observe(self, fingerprint: frozenset[int]) -> CoverageObservation:
        valid_fingerprint, fingerprint_mask = self.fingerprint_to_mask(fingerprint)

        if not self.enabled:
            novelty_class = "none"
            self._record_novelty(novelty_class)
            return CoverageObservation(
                fingerprint=valid_fingerprint,
                fingerprint_mask=fingerprint_mask,
                hash_index=-1,
                novelty_class=novelty_class,
                novelty_score=self.novelty_weights[novelty_class],
                bitmap_occupancy=0.0,
                epoch=self.epoch,
                rotated=False,
            )

        hash_index = self.hash_mask(fingerprint_mask)
        current_seen = self.current_map[hash_index] != 0
        previous_seen = self.previous_map[hash_index] != 0

        if not current_seen and not previous_seen:
            novelty_class = "strong"
        elif not current_seen:
            novelty_class = "weak"
        else:
            novelty_class = "none"

        if not current_seen:
            self.current_map[hash_index] = 1
            self.current_nonzero_count += 1

        self._record_novelty(novelty_class)

        rotated = False
        if self.bitmap_occupancy() >= self.occupancy_rotate_threshold:
            self._rotate_epoch()
            rotated = True

        return CoverageObservation(
            fingerprint=valid_fingerprint,
            fingerprint_mask=fingerprint_mask,
            hash_index=hash_index,
            novelty_class=novelty_class,
            novelty_score=self.novelty_weights[novelty_class],
            bitmap_occupancy=self.bitmap_occupancy(),
            epoch=self.epoch,
            rotated=rotated,
        )

    def update(self, fingerprint: frozenset[int]) -> frozenset[int]:
        self.observe(fingerprint)
        return frozenset()

    def save(self, path: str | Path) -> None:
        payload = {
            "version": COVERAGE_CHECKPOINT_VERSION,
            "mode": self.mode,
            "enabled": self.enabled,
            "map_size_bytes": self.map_size_bytes,
            "occupancy_rotate_threshold": self.occupancy_rotate_threshold,
            "epoch": self.epoch,
            "current_nonzero_count": self.current_nonzero_count,
            "current_map_b64": base64.b64encode(bytes(self.current_map)).decode("ascii"),
            "previous_map_b64": base64.b64encode(bytes(self.previous_map)).decode("ascii"),
            "pocket_residue_ids": list(self._sorted_pocket_residue_ids),
            "residue_to_bit_index": {
                str(residue_id): bit_index
                for residue_id, bit_index in self.residue_to_bit_index.items()
            },
            "novelty_counts": self.novelty_counts(),
        }
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_legacy_payload(self, payload: Mapping[str, object]) -> None:
        saved_pocket_residue_ids = {
            int(value) for value in payload.get("pocket_residue_ids", [])  # type: ignore[arg-type]
        }
        if self.pocket_residue_ids and saved_pocket_residue_ids:
            if self.pocket_residue_ids != saved_pocket_residue_ids:
                raise ValueError("Coverage checkpoint pocket residues differ from target pocket")
        self._set_pocket_residue_ids(saved_pocket_residue_ids)
        self.current_map = bytearray(self.map_size_bytes)
        self.previous_map = bytearray(self.map_size_bytes)
        self.current_nonzero_count = 0
        self.epoch = 0
        self.strong_novelty_count = 0
        self.weak_novelty_count = 0
        self.none_novelty_count = 0

    def _decode_bitmap(self, encoded: str, label: str) -> bytearray:
        decoded = base64.b64decode(encoded.encode("ascii"))
        if len(decoded) != self.map_size_bytes:
            raise ValueError(
                f"Coverage checkpoint {label} length {len(decoded)} does not match map size "
                f"{self.map_size_bytes}"
            )
        return bytearray(decoded)

    def load(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))

        if not isinstance(payload, Mapping):
            raise ValueError("Coverage checkpoint payload must be a JSON object")

        version = int(payload.get("version", 0))
        if version <= 0:
            self._load_legacy_payload(payload)
            return

        map_size_bytes, _threshold = validate_coverage_settings(
            map_size_bytes=int(payload.get("map_size_bytes", self.map_size_bytes)),
            occupancy_rotate_threshold=float(
                payload.get(
                    "occupancy_rotate_threshold",
                    self.occupancy_rotate_threshold,
                )
            ),
            mode=str(payload.get("mode", self.mode)),
        )
        if map_size_bytes != self.map_size_bytes:
            raise ValueError(
                "Coverage checkpoint map size does not match current coverage configuration"
            )

        saved_pocket_residue_ids = {
            int(value) for value in payload.get("pocket_residue_ids", [])
        }
        if self.pocket_residue_ids and saved_pocket_residue_ids:
            if self.pocket_residue_ids != saved_pocket_residue_ids:
                raise ValueError("Coverage checkpoint pocket residues differ from target pocket")
        self._set_pocket_residue_ids(saved_pocket_residue_ids)

        saved_mapping = {
            int(residue_id): int(bit_index)
            for residue_id, bit_index in payload.get("residue_to_bit_index", {}).items()
        }
        if saved_mapping and saved_mapping != self.residue_to_bit_index:
            raise ValueError("Coverage checkpoint residue mapping does not match sorted pocket order")

        self.current_map = self._decode_bitmap(
            str(payload.get("current_map_b64", "")),
            "current_map_b64",
        )
        self.previous_map = self._decode_bitmap(
            str(payload.get("previous_map_b64", "")),
            "previous_map_b64",
        )
        self.current_nonzero_count = sum(1 for value in self.current_map if value)
        self.epoch = int(payload.get("epoch", 0))

        novelty_counts = payload.get("novelty_counts", {})
        self.strong_novelty_count = int(novelty_counts.get("strong", 0))
        self.weak_novelty_count = int(novelty_counts.get("weak", 0))
        self.none_novelty_count = int(novelty_counts.get("none", 0))
