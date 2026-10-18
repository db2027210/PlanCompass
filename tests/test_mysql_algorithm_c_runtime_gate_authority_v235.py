import copy
import json

import pytest

from scripts import mysql_algorithm_c_runtime_gate_authority_v1 as subject


def _target(worker_id: str, container_id: str) -> dict:
    lane = "1" if worker_id.endswith("w1") else "2"
    return {
        "scene": "hybench_xp_cpu",
        "worker_id": worker_id,
        "container": f"xp_cpu_{lane}",
        "container_id": container_id,
        "host_port": 65330 + int(lane),
        "database": "hybench_sf10x",
        "database_cpuset": "176-177" if lane == "1" else "178-179",
        "database_cpuset_mems": "1",
        "client_cpuset": "16-19" if lane == "1" else "20-23",
        "client_cpuset_mems": "0",
        "cpu_quota_cores": 1.65,
        "memory_bytes": 8_589_934_592,
        "swap_bytes": 0,
        "memory_swappiness": 0,
        "pids_max": 1024,
        "io_read_bps": 1_205_862_400,
        "io_write_bps": 1_205_862_400,
        "ready": True,
        "blkio_authority": {"cgroup_path": f"/cgroup/{container_id}"},
        "post_restart_swappiness_readback": {
            "container_id": container_id,
            "readback_passed": True,
        },
    }


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_reset_result_creates_new_immutable_gate_generation(tmp_path):
    old1, old2 = "1" * 64, "2" * 64
    new1 = "3" * 64
    targets = [_target("xp_cpu_w1", old1), _target("xp_cpu_w2", old2)]
    filler = [
        {"worker_id": f"filler_{index}", "scene": "job_memory"}
        for index in range(6)
    ]
    source = tmp_path / "prepared.json"
    _write(source, {"status": "PREPARED", "workers": filler + targets})
    ledger = subject.RuntimeGateAuthority(tmp_path / "ledger", source)
    generation0 = ledger.current_path
    generation0_sha = subject.sha(generation0)

    workers = {
        "xp_cpu_w1": _target("xp_cpu_w1", new1),
        "xp_cpu_w2": _target("xp_cpu_w2", old2),
    }
    reset = tmp_path / "reset_binding.json"
    _write(reset, {
        "status": "PASS",
        "reset_worker_ids": ["xp_cpu_w1"],
        "workers": list(workers.values()),
    })
    generation1 = ledger.refresh(workers, reset)
    value = subject.load(generation1)

    assert generation1 != generation0
    assert subject.sha(generation0) == generation0_sha
    assert value["runtime_gate_identity_release"]["generation"] == 1
    assert value["runtime_gate_identity_release"]["predecessor_sha256"] == generation0_sha
    assert {
        row["container_id"]
        for row in value["workers"]
        if row["worker_id"] == "xp_cpu_w1"
    } == {new1}


def test_reset_result_cannot_change_frozen_envelope(tmp_path):
    old1, old2 = "1" * 64, "2" * 64
    targets = [_target("xp_cpu_w1", old1), _target("xp_cpu_w2", old2)]
    filler = [
        {"worker_id": f"filler_{index}", "scene": "job_memory"}
        for index in range(6)
    ]
    source = tmp_path / "prepared.json"
    _write(source, {"status": "PREPARED", "workers": filler + targets})
    ledger = subject.RuntimeGateAuthority(tmp_path / "ledger", source)
    workers = {row["worker_id"]: copy.deepcopy(row) for row in targets}
    workers["xp_cpu_w1"]["cpu_quota_cores"] = 1.66
    reset = tmp_path / "reset_binding.json"
    _write(reset, {
        "status": "PASS",
        "reset_worker_ids": ["xp_cpu_w1"],
        "workers": list(workers.values()),
    })

    with pytest.raises(RuntimeError, match="envelope differs"):
        ledger.refresh(workers, reset)
