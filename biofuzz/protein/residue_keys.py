from __future__ import annotations

from collections.abc import Iterable

ResidueKey = str
UNKNOWN_CHAIN_ID = "_"


def normalize_chain_id(chain_id: str | None) -> str:
    normalized = (chain_id or "").strip()
    return normalized or UNKNOWN_CHAIN_ID


def residue_key(chain_id: str | None, residue_id: str | int) -> ResidueKey:
    return f"{normalize_chain_id(chain_id)}:{int(str(residue_id).strip())}"


def split_residue_key(value: str | int) -> tuple[str | None, int]:
    if isinstance(value, int):
        return None, int(value)

    text = str(value).strip()
    if not text:
        raise ValueError("Residue identifier cannot be empty")
    if ":" not in text:
        return None, int(text)

    chain_id, residue_id = text.split(":", 1)
    return normalize_chain_id(chain_id), int(residue_id.strip())


def normalize_residue_key(value: str | int) -> ResidueKey:
    chain_id, residue_id = split_residue_key(value)
    if chain_id is None:
        return str(residue_id)
    return residue_key(chain_id, residue_id)


def normalize_residue_keys(values: Iterable[str | int]) -> set[ResidueKey]:
    return {normalize_residue_key(value) for value in values}


def is_qualified_residue_key(value: str | int) -> bool:
    return split_residue_key(value)[0] is not None


def residue_number(value: str | int) -> int:
    return split_residue_key(value)[1]


def sort_residue_keys(values: Iterable[str | int]) -> list[ResidueKey]:
    normalized = [normalize_residue_key(value) for value in values]

    def sort_key(value: ResidueKey) -> tuple[int, str, int, str]:
        chain_id, residue_id = split_residue_key(value)
        if chain_id is None:
            return (0, "", residue_id, value)
        return (1, chain_id, residue_id, value)

    return sorted(normalized, key=sort_key)
