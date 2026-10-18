from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v3"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def test_four_scene_successor_is_explicit_and_fail_closed() -> None:
    manifest = json.loads((FREEZE / "manifest.json").read_text(encoding="utf-8"))
    freeze = json.loads((FREEZE / "four_scene_freeze.json").read_text(encoding="utf-8"))

    expected = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
    assert manifest["active_scenes"] == expected
    assert freeze["active_scenes"] == expected
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["launch_performed"] is False
    assert set(manifest["accepted_rounds"].values()) == {0}
    assert manifest["predecessor"]["sha256"] == _sha(
        ROOT / manifest["predecessor"]["path"]
    )
    assert manifest["artifacts"]["four_scene_freeze.json"] == _sha(
        FREEZE / "four_scene_freeze.json"
    )
    assert manifest["artifacts"]["common_outer_loop.py"] == _sha(
        ROOT / "common_outer_loop.py"
    )
    assert manifest["artifacts"]["mysql_parameter_optimizer.py"] == _sha(
        ROOT / "mysql_parameter_optimizer.py"
    )


def test_native_xp_cpu_and_io_workloads_are_identical() -> None:
    freeze = json.loads((FREEZE / "four_scene_freeze.json").read_text(encoding="utf-8"))
    contracts = freeze["workload_contracts"]
    cpu = contracts["hybench_xp_cpu"]
    io = contracts["hybench_xp_io"]
    for key in ("benchmark", "iq1_iq7", "at1_at6_percent", "xap_xtp_ratio", "duration_seconds"):
        assert cpu[key] == io[key]
    assert cpu["target_resource"] == "cpu"
    assert io["target_resource"] == "io"
    assert cpu["workload_mix_override_prohibited"] is True
    assert io["workload_mix_override_prohibited"] is True


def test_xp_io_workers_are_independent_and_disjoint() -> None:
    freeze = json.loads((FREEZE / "four_scene_freeze.json").read_text(encoding="utf-8"))
    first, second = freeze["xp_io_workers"]
    assert first["host_port"] != second["host_port"]
    assert not (_cpus(first["database_cpuset"]) & _cpus(second["database_cpuset"]))
    assert not (_cpus(first["client_cpuset"]) & _cpus(second["client_cpuset"]))
    assert not (_cpus(first["database_cpuset"]) & _cpus(first["client_cpuset"]))
    assert not (_cpus(second["database_cpuset"]) & _cpus(second["client_cpuset"]))
    for worker in (first, second):
        assert worker["swap_bytes"] == 0
        assert worker["io_read_bps"] == 29360128
        assert worker["io_write_bps"] == 524288


def test_algorithm_c_three_tier_contract_is_frozen() -> None:
    freeze = json.loads((FREEZE / "four_scene_freeze.json").read_text(encoding="utf-8"))
    acceptance = freeze["algorithm_c_acceptance"]
    assert acceptance["tiers"] == ["strict_three_level", "qps_target", "fallback_qps"]
    assert acceptance["downgrade_after_consecutive_failed_outer_rounds"] == 5
    assert acceptance["fallback_acceptance_signal"] == "database_qps_strictly_increased"
    assert acceptance["fallback_still_requires_immutable_hard_gates"] is True
