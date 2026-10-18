from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v4"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def test_v4_is_four_scene_fail_closed_successor() -> None:
    manifest = json.loads((FREEZE_DIR / "manifest.json").read_text())
    freeze = json.loads((FREEZE_DIR / "four_scene_resource_successor.json").read_text())
    assert freeze["active_scenes"] == [
        "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
    ]
    assert freeze["formal_scope"] == "mysql_only"
    assert freeze["formal_launch_authorized"] is False
    assert manifest["formal_launch_authorized"] is False
    assert set(freeze["accepted_rounds"].values()) == {0}
    assert manifest["artifacts"]["four_scene_resource_successor.json"] == _sha(
        FREEZE_DIR / "four_scene_resource_successor.json"
    )
    assert manifest["predecessor_manifest"]["sha256"] == _sha(
        ROOT / manifest["predecessor_manifest"]["path"]
    )


def test_all_eight_database_and_client_sets_are_pairwise_disjoint() -> None:
    freeze = json.loads((FREEZE_DIR / "four_scene_resource_successor.json").read_text())
    assignments: list[tuple[str, set[int]]] = []
    for worker in freeze["workers"]:
        assignments.append((worker["worker_id"] + ":db", _cpus(worker["database_cpuset"])))
        assignments.append((worker["worker_id"] + ":client", _cpus(worker["client_cpuset"])))
    for index, (left_name, left) in enumerate(assignments):
        for right_name, right in assignments[index + 1 :]:
            assert not (left & right), f"{left_name} overlaps {right_name}"


def test_v4_does_not_overlap_observed_pg_affinities() -> None:
    freeze = json.loads((FREEZE_DIR / "four_scene_resource_successor.json").read_text())
    mysql_cpus = set().union(*[
        _cpus(worker["database_cpuset"]) | _cpus(worker["client_cpuset"])
        for worker in freeze["workers"]
    ])
    pg = freeze["known_co_resident_pg_affinity_at_successor_creation"]
    pg_cpus = set().union(*[
        _cpus(value)
        for value in pg["controller_cpusets"] + pg["database_container_cpusets"]
    ])
    assert not (mysql_cpus & pg_cpus)


def test_algorithm_and_native_xp_contracts_are_preserved() -> None:
    freeze = json.loads((FREEZE_DIR / "four_scene_resource_successor.json").read_text())
    acceptance = freeze["algorithm_c_acceptance"]
    assert acceptance["tiers"] == ["strict_three_level", "qps_target", "fallback_qps"]
    assert acceptance["downgrade_after_consecutive_failed_outer_rounds"] == 5
    assert acceptance["immutable_hard_gates_remain_required"] is True
    assert freeze["native_xp_contract"]["cpu_and_io_workload_identical"] is True
