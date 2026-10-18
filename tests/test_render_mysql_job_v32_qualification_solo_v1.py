from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "shell/render_mysql_job_v32_qualification_solo_v1.py"
COLLECTOR_PATH = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDERER = load_module(RENDERER_PATH, "job_qualification_renderer")
COLLECTOR = load_module(COLLECTOR_PATH, "job_qualification_collector")


def client_inspect() -> dict:
    return {
        "Id": "a" * 64,
        "Name": "/job-client",
        "Image": "sha256:" + ("b" * 64),
        "Created": "2026-07-31T00:00:00Z",
        "Config": {
            "Image": "sha256:" + ("b" * 64),
            "Entrypoint": ["/bin/sh"],
            "Cmd": ["sleep", "2147483647"],
            "User": "1008:1008",
            "Labels": {"scenario": "job_memory"},
        },
        "HostConfig": {
            "CpuPeriod": 100000,
            "CpuQuota": 200000,
            "CpusetCpus": "0-2,36-38",
            "CpusetMems": "0",
            "Memory": 2147483648,
            "MemorySwap": 2147483648,
            "PidsLimit": 256,
            "ReadonlyRootfs": True,
            "NetworkMode": "host",
            "SecurityOpt": ["no-new-privileges:true"],
            "CapDrop": ["ALL"],
            "LogConfig": {"Type": "none", "Config": {}},
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/sealed/bundle",
                "Destination": "/sealed/bundle",
                "RW": False,
            }
        ],
    }


def test_atomic_json_writes_required_sha256_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "qualification_command.json"
    RENDERER.atomic_json(path, {"z": 1, "a": 2})
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.read_text(encoding="ascii") == (
        hashlib.sha256(path.read_bytes()).hexdigest()
        + "  qualification_command.json\n"
    )


def test_renderer_client_identity_matches_collector_canonical_projection() -> None:
    row = client_inspect()
    rendered = RENDERER.sha_bytes(
        RENDERER.collector_canonical(RENDERER.client_projection(row))
    )
    collected = COLLECTOR.sha_bytes(
        COLLECTOR.canonical(
            COLLECTOR._client_container_immutable_projection(row)
        )
    )
    assert rendered == collected


def test_plan_writes_collector_output_under_host_measurement_tmpfs(
    tmp_path: Path,
) -> None:
    attempt = "job-v2-20260731t000000z-renderer-test-0123456789ab"
    attempt_root = "/runtime/attempts/" + attempt
    spec = {
        "attempt_token": attempt,
        "bundle": {
            "path": "/runtime/bundles/" + ("c" * 64),
        },
        "python_runtime": {"executable_path": "/runtime/python/bin/python"},
        "collector": "/runtime/bundles/collector.py",
        "containers": {"database": "job-db", "client": "job-client"},
        "mysql_defaults_file": attempt_root + "/credentials/mysql.cnf",
        "paths": {
            "measurement_tmpfs": attempt_root + "/measurement",
        },
    }
    plan = RENDERER.command_plan(
        spec,
        tmp_path,
        {"attempt": attempt},
    )
    output_index = plan["argv_with_token_placeholder"].index("--output") + 1
    assert plan["argv_with_token_placeholder"][output_index] == (
        attempt_root + "/measurement/qualification_solo_evidence.json"
    )


def test_input_binding_is_derived_from_successor_bootstrap_evidence() -> None:
    attempt = "job-v2-20260731t000000z-successor-test-0123456789ab"
    bundle_id = "d" * 64
    database_pid = 1234
    client_pid = 5678
    client = client_inspect()
    spec = {
        "attempt_token": attempt,
        "bundle": {
            "bundle_id": bundle_id,
            "manifest_sha256": "e" * 64,
        },
        "paths": {
            "attempt_root":
                "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
                "runtime_algorithm_c_successor_20260729/attempts/" + attempt,
        },
    }
    evidence = {
        "database": {"pid": database_pid, "inspect": {"Id": "f" * 64}},
        "client": {"pid": client_pid, "inspect": client},
        "collect_only": {
            "document": {
                "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
                "bundle_id": bundle_id,
                "container": {"pid": database_pid},
            }
        },
    }
    binding = RENDERER.validate_inputs(spec, evidence)
    assert binding["bundle_id"] == bundle_id
    assert binding["database_pid"] == database_pid
    assert binding["client_pid"] == client_pid
    assert binding["client_cpuset"] == "0-2,36-38"
    assert binding["client_mems"] == "0"
