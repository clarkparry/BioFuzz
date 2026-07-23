from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from biofuzz.triage.record import TriageRecord


def write_report(records: list[TriageRecord], output_dir: str | Path, top_n: int = 20) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "report.json"
    report_data = [r.to_report_dict() for r in records]
    report_path.write_text(json.dumps(report_data, indent=2))

    _write_html_report(records, output_dir / "report.html")
    _write_top_hits(records, output_dir / "top_hits", top_n)

    return report_path


def _write_html_report(records: list[TriageRecord], path: Path) -> None:
    ranked = sorted(records, key=lambda r: r.overall_rank or 10**9)
    rows = "\n".join(
        f"<tr><td>{r.overall_rank}</td><td>{r.smiles}</td>"
        f"<td>{r.confirmed_affinity}</td><td>{r.ligand_efficiency}</td>"
        f"<td>{', '.join(r.flags)}</td><td>{', '.join(r.filters_failed)}</td></tr>"
        for r in ranked
    )
    path.write_text(
        "<html><body><table border='1'>"
        "<tr><th>Rank</th><th>SMILES</th><th>Confirmed Affinity</th>"
        "<th>Ligand Efficiency</th><th>Flags</th><th>Filters Failed</th></tr>"
        f"{rows}</table></body></html>"
    )


def _write_top_hits(records: list[TriageRecord], top_hits_dir: Path, top_n: int) -> None:
    top_hits_dir.mkdir(parents=True, exist_ok=True)
    ranked = sorted(
        (r for r in records if r.overall_rank is not None), key=lambda r: r.overall_rank
    )[:top_n]

    for i, record in enumerate(ranked, start=1):
        smiles_hash = hashlib.sha1(record.smiles.encode()).hexdigest()[:8]
        hit_dir = top_hits_dir / f"{i:03d}_{smiles_hash}"
        hit_dir.mkdir(parents=True, exist_ok=True)

        if record.pose_path and Path(record.pose_path).exists():
            shutil.copyfile(record.pose_path, hit_dir / "pose.pdbqt")

        (hit_dir / "metadata.json").write_text(json.dumps(record.to_report_dict(), indent=2))

        summary_lines = [
            f"SMILES: {record.smiles}",
            f"Overall rank: {record.overall_rank}",
            f"Confirmed affinity: {record.confirmed_affinity}",
            f"Ligand efficiency: {record.ligand_efficiency}",
            f"Vina affinity: {record.vina_affinity}",
            f"CNN affinity (kcal/mol): {record.cnn_affinity_kcal}",
            f"CNN pose score: {record.cnn_pose_score}",
            f"Selectivity ddG (kcal/mol): {record.selectivity_ddg}",
            f"Selectivity (fold): {record.selectivity_fold}",
            f"Flags: {', '.join(record.flags) or 'none'}",
            f"Filters failed: {', '.join(record.filters_failed) or 'none'}",
        ]
        (hit_dir / "summary.txt").write_text("\n".join(summary_lines) + "\n")
