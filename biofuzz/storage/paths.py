from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass(frozen=True)
class RunLayout:
    root: Path
    corpus_dir: Path
    findings_dir: Path
    cache_dir: Path
    coverage_path: Path
    log_path: Path


def make_run_dir(base_dir: Path | str, target: str, stamp: str | None = None, now: datetime | None = None) -> RunLayout:
    base_dir = Path(base_dir)
    if stamp is None:
        stamp = (now.date() if now else date.today()).isoformat()

    name = f"{stamp}_{target}"
    root = base_dir / name
    counter = 1
    while root.exists():
        counter += 1
        root = base_dir / f"{name}_{counter}"

    corpus_dir = root / "corpus"
    findings_dir = root / "findings"
    cache_dir = root / "cache"
    for d in (corpus_dir, findings_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    return RunLayout(
        root=root,
        corpus_dir=corpus_dir,
        findings_dir=findings_dir,
        cache_dir=cache_dir,
        coverage_path=root / "coverage.json",
        log_path=root / "fuzzer.log",
    )
