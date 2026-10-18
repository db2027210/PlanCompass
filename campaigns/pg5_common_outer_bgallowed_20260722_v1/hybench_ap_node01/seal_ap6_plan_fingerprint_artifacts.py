#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


EXPECTED_RANKING_SHA256 = (
    "6d0a1f930876afc1ea005bbb4e5a45017c5c9bc48dbfc6566809b23d305e73ab"
)
EXPECTED_CORE_SHA256 = {
    "common_outer_loop.py": (
        "61a8b15483f486229f7463df5b1caa68de3211a93794fede7d9c26886d5e7631"
    ),
    "parameter_optimizer.py": (
        "13120404a519acf081a32466e51499c17b38b915c6d23a5f619075ad75692f5f"
    ),
    "resource_utility.py": (
        "cf823efe25d13c90a69cb91deff7b0e2ad805d594ab5bc40d3ed18241d3fd8a2"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON : {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--core-dir", type=Path, required=True)
    args = parser.parse_args()
    artifact_dir = args.artifact_dir.resolve()
    core_dir = args.core_dir.resolve()

    ranking_path = artifact_dir / "subset_ranking_with_plan_fingerprints.json"
    if sha256(ranking_path) != EXPECTED_RANKING_SHA256:
        raise RuntimeError("annotated ranking SHA256 mismatch")
    ranking = read_json(ranking_path)
    candidates = ranking.get("candidate_rankings")
    if not isinstance(candidates, list) or len(candidates) != 63:
        raise RuntimeError("expected exactly 63 candidate rankings")

    raw_artifacts: List[Dict[str, Any]] = []
    clusters: Dict[str, List[int]] = {}
    for rank in range(1, 64):
        path = artifact_dir / f"rank_{rank:02d}_plan_fingerprint.json"
        payload = read_json(path)
        if payload.get("rank") != rank or payload.get("status") != "complete":
            raise RuntimeError(f"rank {rank}: incomplete plan artifact")
        query_ids = payload.get("query_ids")
        raw_explain = payload.get("raw_explain_json_by_query")
        exact_plans = payload.get("exact_structural_plans")
        if query_ids != list(range(1, 14)):
            raise RuntimeError(f"rank {rank}: query IDs are not AP1..AP13")
        if not isinstance(raw_explain, dict) or set(raw_explain) != {
            str(query_id) for query_id in range(1, 14)
        }:
            raise RuntimeError(f"rank {rank}: raw EXPLAIN bundle is incomplete")
        if not isinstance(exact_plans, dict) or set(exact_plans) != set(raw_explain):
            raise RuntimeError(f"rank {rank}: exact structural bundle is incomplete")
        cluster_id = payload.get("plan_similarity_cluster_id")
        fingerprint = payload.get("workload_plan_fingerprint")
        if cluster_id != fingerprint or not str(cluster_id).startswith(
            "ap13-exact-sha256:"
        ):
            raise RuntimeError(f"rank {rank}: cluster is not the exact bundle hash")
        clusters.setdefault(str(cluster_id), []).append(rank)
        raw_artifacts.append(
            {
                "rank": rank,
                "path": str(path),
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
                "raw_explain_query_count": len(raw_explain),
                "exact_plan_query_count": len(exact_plans),
                "exact_plan_cluster_id": cluster_id,
            }
        )

    if len(clusters) != 8:
        raise RuntimeError(f"expected 8 exact plan clusters, got {len(clusters)}")

    auxiliary_names = [
        "ap13_plan_query_fixture.json",
        "plan_fingerprint_progress.json",
        "subset_ranking_with_plan_fingerprints.json",
        "ap6_model_error_box_status.json",
        "ap6_replay_selection_plan.json",
    ]
    auxiliary = []
    for name in auxiliary_names:
        path = artifact_dir / name
        auxiliary.append(
            {
                "name": name,
                "path": str(path),
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )

    core = []
    for name, expected in EXPECTED_CORE_SHA256.items():
        path = core_dir / name
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"final core mismatch: {name}={actual}")
        core.append({"name": name, "path": str(path), "sha256": actual})

    cluster_rows = [
        {
            "exact_plan_cluster_id": cluster_id,
            "member_ranks": ranks,
            "representative_rank": min(
                ranks,
                key=lambda rank: next(
                    index
                    for index, candidate in enumerate(candidates)
                    if candidate.get("rank") == rank
                ),
            ),
        }
        for cluster_id, ranks in clusters.items()
    ]
    cluster_rows.sort(key=lambda row: row["representative_rank"])
    manifest = {
        "schema_version": 1,
        "status": "sealed_complete",
        "workload": "HyBench AP1..AP13",
        "formal_active_parameter_count": 6,
        "candidate_count": 63,
        "raw_explain_artifact_count": len(raw_artifacts),
        "raw_explain_query_count_per_candidate": 13,
        "exact_plan_cluster_count": len(cluster_rows),
        "cluster_semantics": (
            "exact_AP1_AP13_physical_structure_bundle_equality_v1"
        ),
        "approximate_structure_role": "audit_only_never_suppresses_real_replay",
        "raw_explain_artifacts": raw_artifacts,
        "exact_plan_clusters": cluster_rows,
        "auxiliary_artifacts": auxiliary,
        "final_common_core": core,
    }
    output = artifact_dir / "ap6_plan_fingerprint_seal_manifest.json"
    write_json(output, manifest)
    print(
        json.dumps(
            {
                "manifest_path": str(output),
                "manifest_sha256": sha256(output),
                "raw_explain_artifact_count": len(raw_artifacts),
                "exact_plan_cluster_count": len(cluster_rows),
                "representative_ranks": [
                    row["representative_rank"] for row in cluster_rows
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
