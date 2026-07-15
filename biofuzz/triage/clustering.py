from __future__ import annotations

from biofuzz.docker.parser import parse_pose
from biofuzz.triage.record import TriageRecord

CLUSTER_DISTANCE_THRESHOLD = 2.0  # Angstroms, center-of-mass distance


def _center_of_mass(record: TriageRecord) -> tuple[float, float, float] | None:
    try:
        atoms = parse_pose(open(record.pose_path).read())
    except (FileNotFoundError, OSError):
        return None
    if not atoms:
        return None
    n = len(atoms)
    return (
        sum(a.x for a in atoms) / n,
        sum(a.y for a in atoms) / n,
        sum(a.z for a in atoms) / n,
    )


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def cluster_records(records: list[TriageRecord]) -> None:
    """Assigns cluster_id and cluster_rank in place, using pose
    center-of-mass distance (the doc's documented fallback when full pose
    RMSD superposition isn't available -- re-confirmed poses aren't
    persisted past the confirmation stage, so this uses each finding's
    original saved pose instead)."""
    centers: list[tuple[float, float, float] | None] = [_center_of_mass(r) for r in records]

    cluster_ids: list[int | None] = [None] * len(records)
    next_cluster_id = 0
    cluster_centers: list[tuple[float, float, float]] = []

    for i, center in enumerate(centers):
        if center is None:
            cluster_ids[i] = next_cluster_id
            cluster_centers.append((0.0, 0.0, 0.0))
            next_cluster_id += 1
            continue

        assigned = None
        for cid, ccenter in enumerate(cluster_centers):
            if _distance(center, ccenter) <= CLUSTER_DISTANCE_THRESHOLD:
                assigned = cid
                break

        if assigned is None:
            assigned = next_cluster_id
            cluster_centers.append(center)
            next_cluster_id += 1

        cluster_ids[i] = assigned

    for record, cid in zip(records, cluster_ids):
        record.cluster_id = cid

    clusters: dict[int, list[TriageRecord]] = {}
    for record in records:
        clusters.setdefault(record.cluster_id, []).append(record)

    for members in clusters.values():
        members.sort(key=lambda r: r.ligand_efficiency or 0.0, reverse=True)
        for rank, member in enumerate(members, start=1):
            member.cluster_rank = rank
