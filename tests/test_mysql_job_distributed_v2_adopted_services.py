from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/mysql_job_distributed_v2_adopted_services.py"
MONITOR_PATH = ROOT / "shell/mysql_job_distributed_v2_rolling_monitor.py"
BUILDER_PATH = ROOT / "shell/build_mysql_job_distributed_v2_successor.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVICES = load_module(MODULE_PATH, "job_v2_adopted_services")
MONITOR = load_module(MONITOR_PATH, "job_v2_rolling_monitor")
BUILDER = load_module(BUILDER_PATH, "job_v2_runtime_binding_builder")


def rolling_row(index: int, epoch_ms: int) -> dict:
    return {
        "schema_version": "mysql_job_distributed_v2_rolling_gate_sample_v1",
        "attempt_token": "job-v2-" + ("a" * 32),
        "point_token": "point-1",
        "index": index,
        "epoch_ms": epoch_ms,
        "database_container_id": "b" * 64,
        "database_pid": 3001,
        "client_container_id": "c" * 64,
        "client_pid": 3002,
        "client_target_device": {"read_bytes": 0, "write_bytes": 0},
        "resource_stream": {},
        "status": "PASS_SAMPLE",
        "accepted_round_authorized": False,
    }


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            SERVICES.ROLLING_MARKER
            + json.dumps(row, sort_keys=True)
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def test_rolling_window_accepts_181_constant_identity_samples(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rolling.jsonl"
    start = 2_000_000_000_000
    write_rows(
        path,
        [rolling_row(index, start + index * 1000) for index in range(181)],
    )
    result = SERVICES.parse_rolling_gate_window(
        path,
        attempt_token="job-v2-" + ("a" * 32),
        point_token="point-1",
        start_epoch_ms=start,
        measurement_seconds=180,
    )
    assert result["status"] == "PASS"
    assert result["sample_count"] == 181
    assert result["full_host_inventory_passed_every_sample"] is True
    assert result["accepted_round_authorized"] is False


def test_rolling_window_fails_on_gap_or_client_device_access(
    tmp_path: Path,
) -> None:
    start = 2_000_000_000_000
    rows = [rolling_row(index, start + index * 1000) for index in range(181)]
    path = tmp_path / "rolling.jsonl"
    rows[90]["epoch_ms"] += 700
    write_rows(path, rows)
    with pytest.raises(SERVICES.AdoptionError, match="gap"):
        SERVICES.parse_rolling_gate_window(
            path,
            attempt_token="job-v2-" + ("a" * 32),
            point_token="point-1",
            start_epoch_ms=start,
            measurement_seconds=180,
        )
    rows = [rolling_row(index, start + index * 1000) for index in range(181)]
    rows[90]["client_target_device"]["write_bytes"] = 1
    write_rows(path, rows)
    with pytest.raises(SERVICES.AdoptionError, match="target MySQL device"):
        SERVICES.parse_rolling_gate_window(
            path,
            attempt_token="job-v2-" + ("a" * 32),
            point_token="point-1",
            start_epoch_ms=start,
            measurement_seconds=180,
        )


def test_monitor_inventory_is_fail_closed() -> None:
    stream = {
        "process_cpu_affinity_and_device_inventory": {
            "affinity_exact": True,
            "rolling_full_host_inventory": {
                "inventory_complete": True,
                "conflicts": [],
                "permission_failure_count": 0,
                "race_count": 0,
                "foreign_writer_count": 0,
            },
            "client_cgroup_devices": {
                "259:3": {"read_bytes": 0, "write_bytes": 0}
            },
        }
    }
    assert MONITOR._validate_inventory(stream, "259:3") == {
        "read_bytes": 0,
        "write_bytes": 0,
    }
    broken = json.loads(json.dumps(stream))
    broken["process_cpu_affinity_and_device_inventory"][
        "rolling_full_host_inventory"
    ]["inventory_complete"] = False
    with pytest.raises(MONITOR.MonitorError, match="failed closed"):
        MONITOR._validate_inventory(broken, "259:3")


def test_service_has_no_recursive_owner_in_copied_config() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert 'mutable["_service_owner"]' not in source
    assert 'self.v2_config["_service_owner"]' not in source
    assert '"owner": self' in source
    assert "new_planner_container_created" in source


def test_runtime_binding_uses_exact_closure_and_manifest_python() -> None:
    built = BUILDER.build()
    bundle = Path(built["bundle"])
    runtime = json.loads(
        (bundle / "distributed_runtime_contract.json").read_text(
            encoding="utf-8"
        )
    )
    config = {
        "distributed_v2": {
            "local_bundle_path": str(bundle),
            "remote_bundle_path": (
                runtime["bundle_remote_path_rule"].replace(
                    "<bundle_id>", built["bundle_id"]
                )
            ),
            "bundle_manifest_sha256":
                built["bundle_manifest_sha256"],
            "remote_python": runtime["toolchain"]["python_runtime"],
        }
    }
    result = SERVICES.validate_distributed_v2_runtime_binding(
        config, {"bundle_id": built["bundle_id"]}
    )
    assert result["status"] == (
        "PASS_LOCAL_EXACT_CLOSURE_AND_REMOTE_PATH_BINDING"
    )
    assert result["remote_python"].endswith("/bin/python3.11")
    config["distributed_v2"]["remote_python"] = (
        runtime["toolchain"]["python_runtime_root"] + "/bin/python"
    )
    with pytest.raises(SERVICES.AdoptionError, match="runtime differs"):
        SERVICES.validate_distributed_v2_runtime_binding(
            config, {"bundle_id": built["bundle_id"]}
        )
