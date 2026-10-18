#!/usr/bin/env python3
"""Fake end-to-end regression for the attempt16 SET LOCAL supervisor."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

from run_tpcc_setlocal_direction_calibration import (
    DEFAULT_PARAMS,
    assess_r0_profile,
    build_parser,
    run_campaign,
    validate_args,
    workload_cwd,
)
from tpcc_session_local_calibration import (
    PARAMETERS,
    SEMANTICS,
    canonical_params,
    write_json_new,
)


class FakeBackend:
    def __init__(self, args: argparse.Namespace, *, fail_id: str = "") -> None:
        self.args = args
        self.fail_id = fail_id
        self.current_id = ""
        self.cpu_ns = 0.0
        self.io_read_bytes = 0.0
        self.io_write_bytes = 0.0
        self.calls = 0.0
        self.clock = 0.0
        self.attempted_points: list[str] = []

    def restore(self) -> None:
        return None

    def verify_profile(self) -> Mapping[str, Any]:
        return {
            "cpu_limit_cores": self.args.cpu_limit_cores,
            "memory_limit_bytes": self.args.memory_limit_bytes,
            "cpuset_cpus": self.args.cpuset_cpus,
            "io_device": self.args.io_device,
            "io_read_bps": int(self.args.io_read_cap_mib_s * 1024 ** 2),
            "io_write_bps": int(self.args.io_write_cap_mib_s * 1024 ** 2),
        }

    def snapshot_defaults(self, output: Path) -> Mapping[str, Any]:
        document = {
            "schema_version": 1,
            "artifact_type": "tpcc_pg_global_defaults_snapshot_v1",
            "auditor_connection_candidate_options": False,
            "parameters": {
                name: {
                    "setting": canonical_params(DEFAULT_PARAMS)[name],
                    "reset_val": canonical_params(DEFAULT_PARAMS)[name],
                    "source": "default",
                    "sourcefile": None,
                }
                for name in PARAMETERS
            },
        }
        write_json_new(output, document)
        return document

    def prepare_bundle(
        self,
        point_dir: Path,
        calibration_id: str,
        params_path: Path,
    ) -> Path:
        self.current_id = calibration_id
        params = json.loads(params_path.read_text(encoding="utf-8"))
        bundle = point_dir / "bundle"
        bundle.mkdir()
        readbacks = bundle / "connection_readbacks.jsonl"
        manifest = {
            "schema_version": 1,
            "artifact_type": "tpcc_session_local_calibration_manifest_v1",
            "calibration_id": calibration_id,
            "candidate_params": params,
            "parameter_semantics": SEMANTICS,
            "connection_contract": {
                "native": {
                    "minimum_connection_readbacks": 1,
                    "application_name": f"fake-native-{calibration_id}",
                },
                "sensitive": {
                    "minimum_connection_readbacks": 1,
                    "application_name": f"fake-sensitive-{calibration_id}",
                },
                "readback_path": str(readbacks),
            },
            "dataset_builder_contract": {
                "seal_artifact_type": "tpcc_session_local_calibration_seal_v1",
                "transaction_local_required": True,
            },
        }
        path = bundle / "session_local_manifest.json"
        write_json_new(path, manifest)
        return path

    def launch(self, manifest_path: Path, point_dir: Path) -> Mapping[str, Any]:
        self.attempted_points.append(self.current_id)
        if self.current_id == self.fail_id:
            raise RuntimeError("synthetic launch failure")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        params = manifest["candidate_params"]
        records = []
        for kind in ("native", "sensitive"):
            connection_id = f"{kind}-{self.current_id}"
            transaction_id = connection_id + ":1"
            common = {
                "schema_version": 1,
                "connection_id": connection_id,
                "transaction_id": transaction_id,
                "transaction_ordinal": 1,
                "client_kind": kind,
                "calibration_id": self.current_id,
            }
            records.extend(
                [
                    {
                        **common,
                        "event": "tpcc_transaction_parameter_readback",
                        "sequence": 1,
                        "readback_phase": "after_set_local_before_business_sql",
                        "application_name": manifest["connection_contract"][kind][
                            "application_name"
                        ],
                        "settings": params,
                    },
                    {
                        **common,
                        "event": "tpcc_transaction_business_sql_dispatch",
                        "sequence": 2,
                        "sql_sha256": "fake",
                    },
                ]
            )
        readback_path = Path(manifest["connection_contract"]["readback_path"])
        readback_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        (point_dir / "native.log").write_text("", encoding="utf-8")
        (point_dir / "sensitive.log").write_text("", encoding="utf-8")
        return {"native": {}, "sensitive": {}}

    def snapshot(self) -> Mapping[str, float]:
        interval = self.args.sample_interval_seconds
        self.cpu_ns += self.args.cpu_limit_cores * 0.88 * interval * 1e9
        io_increment = self.args.io_total_limit_mib_s * 1024 ** 2 * 0.4 * interval
        self.io_read_bytes += io_increment / 2
        self.io_write_bytes += io_increment / 2
        return {
            "cpu_ns": self.cpu_ns,
            "memory_bytes": self.args.memory_limit_bytes * 0.4,
            "io_read_bytes": self.io_read_bytes,
            "io_write_bytes": self.io_write_bytes,
        }

    def db_calls(self) -> float:
        self.calls += 100
        return self.calls

    def wait_interval(self, seconds: float) -> None:
        self.clock += seconds

    def monotonic(self) -> float:
        return self.clock

    def alive(self, processes: Mapping[str, Any]) -> bool:
        return True

    def stop(self, processes: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "process_groups": [],
            "remaining_pids": [],
            "fully_released": True,
        }


def fake_args(root: Path, *, resume: bool) -> argparse.Namespace:
    restore = root / "fake_restore.sh"
    benchbase_xml = root / "fake_benchbase.xml"
    benchbase_jar = root / "fake_benchbase.jar"
    jdbc_jar = root / "fake_jdbc.jar"
    if not restore.exists():
        restore.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        benchbase_xml.write_text("<parameters/>", encoding="utf-8")
        benchbase_jar.write_bytes(b"fake benchbase")
        jdbc_jar.write_bytes(b"fake jdbc")
    return argparse.Namespace(
        output_root=root / "attempt16_setlocal_direction_calibration_fake",
        resume=resume,
        baseline_only=False,
        scopes=3,
        restore_command=restore,
        benchbase_xml=benchbase_xml,
        benchbase_jar=benchbase_jar,
        postgresql_jdbc_jar=jdbc_jar,
        container="fake-pg",
        host="127.0.0.1",
        port=59640,
        database="fullgrid_tpcc",
        user="postgres",
        password_env="FAKE_PGPASSWORD",
        cpu_limit_cores=2.05,
        memory_limit_bytes=4294967296,
        io_device="259:6",
        io_read_cap_mib_s=13.5,
        io_write_cap_mib_s=13.5,
        io_total_limit_mib_s=27.0,
        cpuset_cpus="104-107,360-363",
        native_terminals=1,
        sensitive_clients=1,
        warehouses=10,
        java_xmx="-Xmx1g",
        warmup_seconds=0.0,
        sample_interval_seconds=1.0,
        window_seconds=1.0,
        required_windows=2,
        coverage_min=0.9,
        cv_max=0.1,
        minimum_measurement_seconds=2.0,
        maximum_measurement_seconds=3.0,
    )


def main() -> None:
    os.environ["FAKE_PGPASSWORD"] = "fake"
    formal_args = build_parser().parse_args(
        [
            "--output-root",
            "/tmp/attempt16_setlocal_direction_calibration_validation",
        ]
    )
    validate_args(formal_args)
    assert assess_r0_profile(
        {
            "resource_utilization": {
                "cpu": 0.88,
                "memory": 0.40,
                "io": 0.40,
            }
        }
    )["passed"] is True
    assert assess_r0_profile(
        {
            "resource_utilization": {
                "cpu": 0.79,
                "memory": 0.40,
                "io": 0.40,
            }
        }
    )["passed"] is False
    formal_args.memory_limit_bytes += 1
    try:
        validate_args(formal_args)
    except ValueError as exc:
        assert "resource profile differs" in str(exc)
    else:
        raise AssertionError("profile drift must fail closed")

    with tempfile.TemporaryDirectory(prefix="tpcc-attempt16-fake-") as temp_dir:
        root = Path(temp_dir)
        args = fake_args(root, resume=False)
        runtime_dir = root / "point/bundle/runtime"
        assert workload_cwd("native", args, runtime_dir) == args.benchbase_jar.parent
        assert workload_cwd("sensitive", args, runtime_dir) == runtime_dir
        failed_id = "scope01_seq_page_cost_down"
        backend = FakeBackend(args, fail_id=failed_id)
        assert run_campaign(args, backend) == 0
        assert len(backend.attempted_points) == 45

        scope = (
            args.output_root
            / "calibration/scope_01/tpcc_sensitive/cpu"
        )
        baseline = json.loads((scope / "fresh_baseline.json").read_text(encoding="utf-8"))
        assert baseline["raw"]["double_loop_measurement_usable"] is True
        assert Path(baseline["session_local_calibration_seal"]).is_file()
        rows = [
            json.loads(line)
            for line in (scope / "fresh_model_calibration.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(rows) == 14
        assert sum(row["status"] == "usable" for row in rows) == 13
        assert sum(row["status"] == "technical_failure" for row in rows) == 1
        failure = next(row for row in rows if row["status"] == "technical_failure")
        assert failure["calibration_id"] == failed_id
        for scope_index in (2, 3):
            other_scope = (
                args.output_root
                / f"calibration/scope_{scope_index:02d}/tpcc_sensitive/cpu"
            )
            assert (other_scope / "fresh_baseline.json").is_file()
            other_rows = (
                other_scope / "fresh_model_calibration.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            assert len(other_rows) == 14

        dataset = root / "dataset.json"
        subprocess.run(
            [
                "/home/anonymous/miniconda3/bin/python3",
                "shell/build_replay_direction_dataset.py",
                "--input",
                str(args.output_root / "calibration"),
                "--output",
                str(dataset),
                "--workload",
                "tpcc_sensitive",
                "--target-resource",
                "cpu",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        dataset_document = json.loads(dataset.read_text(encoding="utf-8"))
        assert len(dataset_document["records"]) == 41

        calibration_before = {
            path: path.read_bytes()
            for path in args.output_root.glob(
                "calibration/scope_*/tpcc_sensitive/cpu/fresh_model_calibration.jsonl"
            )
        }
        seals_before = {
            path: path.read_bytes()
            for path in args.output_root.glob(
                "calibration/scope_*/tpcc_sensitive/cpu/"
                "points/*/session_local_calibration_seal.json"
            )
        }
        resume_args = fake_args(root, resume=True)
        resume_backend = FakeBackend(resume_args)
        assert run_campaign(resume_args, resume_backend) == 0
        assert resume_backend.attempted_points == []
        assert {
            path: path.read_bytes()
            for path in args.output_root.glob(
                "calibration/scope_*/tpcc_sensitive/cpu/fresh_model_calibration.jsonl"
            )
        } == calibration_before
        assert {
            path: path.read_bytes()
            for path in args.output_root.glob(
                "calibration/scope_*/tpcc_sensitive/cpu/"
                "points/*/session_local_calibration_seal.json"
            )
        } == seals_before

    source = Path(__file__).with_name(
        "run_tpcc_setlocal_direction_calibration.py"
    ).read_text(encoding="utf-8").upper()
    assert "ALTER SYSTEM" not in source
    print("TPC-C attempt16 fake end-to-end regression passed", flush=True)


if __name__ == "__main__":
    main()
