from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DockingConfig:
    receptor_path: str
    center_x: float
    center_y: float
    center_z: float
    size_x: float
    size_y: float
    size_z: float
    exhaustiveness: int
    num_modes: int
    timeout_seconds: int
    workers: int = 1
    engine_path: str | None = None


@dataclass
class DockingResult:
    success: bool
    log_text: str
    pose_path: str | None
    error: str | None
    completed: bool
    gpu_active: bool | None


class DockingBackend(ABC):
    name: str

    @abstractmethod
    def dock(self, ligand_pdbqt: str, config: DockingConfig) -> DockingResult:
        ...

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def runtime_issues(self) -> list[str]:
        ...


def get_backend(name: str) -> DockingBackend:
    from biofuzz.docker.gnina import GninaBackend

    backends: dict[str, type[DockingBackend]] = {"gnina": GninaBackend}
    if name not in backends:
        raise ValueError(f"Unknown docking backend: {name}")
    return backends[name]()


__all__ = ["DockingBackend", "DockingConfig", "DockingResult", "get_backend"]
