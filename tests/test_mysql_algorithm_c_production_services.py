import json
import subprocess
from types import SimpleNamespace

import mysql_algorithm_c_production_services as algorithm_c
from mysql_algorithm_b_production_services import ProductionServices


def test_algorithm_c_factory_uses_frozen_profile_and_node01_lane(tmp_path) -> None:
    profile = {
        "cpuset": "21-24,57-60",
        "mems": "1",
        "cpu_cores": 4.5,
        "memory_bytes": 8 * 1024**3,
        "pids": 512,
        "read_bps": 1_205_862_400,
        "write_bps": 1_205_862_400,
        "buffer_pool_bytes": 4 * 1024**3,
    }
    profile_path = tmp_path / "tpcc.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    services = ProductionServices({
        "benchmark": "tpcc",
        "method_id": "experience_table_sgd",
        "profile": str(profile_path),
        "output": {"directory": str(tmp_path / "output")},
    })

    assert callable(algorithm_c.build)
    assert services.lane.host == "anonymous@192.0.2.3"
    assert services.lane.hostname == "node01"
    assert services.lane.template.startswith("/mnt/mysql_nvme0n1/")
    assert services.lane.device == "/dev/nvme0n1"
    assert services.lane.cpuset == profile["cpuset"]
    assert services.lane.memory_bytes == profile["memory_bytes"]
    assert services.lane.cpu_cores == 4.5
    assert services._run_id("candidate").startswith("alg_c_tpcc_candidate_")


def test_host02_pressure_probe_retries_transient_ssh_255(
    tmp_path, monkeypatch,
) -> None:
    profile_path = tmp_path / "job.json"
    profile_path.write_text(json.dumps({
        "cpuset": "18-20,54-56",
        "mems": "1",
        "cpu_cores": 1.25,
        "memory_bytes": 3 * 1024**3,
        "pids": 512,
        "read_bps": 12 * 1024**2,
        "write_bps": 12 * 1024**2,
        "buffer_pool_bytes": 2 * 1024**3,
    }), encoding="utf-8")
    services = ProductionServices({
        "benchmark": "job",
        "method_id": "experience_table_sgd",
        "profile": str(profile_path),
        "output": {"directory": str(tmp_path / "output")},
    })
    attempts = []

    def fake_run(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise subprocess.CalledProcessError(255, args[0])
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    delays = []
    monkeypatch.setattr("mysql_algorithm_b_production_services.subprocess.run", fake_run)
    monkeypatch.setattr("mysql_algorithm_b_production_services.time.sleep", delays.append)

    result = services.pressure_probe("job_memory", "before_pressure")

    assert len(attempts) == 2
    assert delays == [2.0]
    assert result["isolation_verified"] is True
    assert result["cross_engine_comparison_prohibited"] is True
