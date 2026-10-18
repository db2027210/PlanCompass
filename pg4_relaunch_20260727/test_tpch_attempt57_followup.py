#!/usr/bin/env python3
"""Local-only tests for the attempt57 follow-up contract."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import tpch_attempt57_followup as contract
import run_tpch_attempt57_weighted_workload as weighted


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    contract.LANE_ROOT = root / "tpch_on_ap_slot_node01"
    contract.ATTEMPT_ROOT = (
        contract.LANE_ROOT
        / "experience_sgd/attempt57_sf10_strict_r0_seeded_cpu7p7_w36_20260727"
    )
    contract.MANIFEST_PATH = (
        contract.ATTEMPT_ROOT / "followup/attempt57_followup_manifest.json"
    )
    contract.CALIBRATION_ROOT = (
        contract.ATTEMPT_ROOT / "followup/direction_calibration"
    )
    contract.CALIBRATION_SUMMARY = (
        contract.CALIBRATION_ROOT / "calibration_summary.json"
    )
    contract.READINESS_PATH = (
        contract.ATTEMPT_ROOT / "followup/outer_launch_readiness.json"
    )
    write_json(
        contract.ATTEMPT_ROOT / "r0_supervisor_state.json",
        {
            "status": "complete",
            "phase": "strict_r0_complete",
            "passed_repeats": 3,
        },
    )
    for repeat, cpu in enumerate((37.76, 45.62, 41.0), start=1):
        point = contract.ATTEMPT_ROOT / "r0" / f"repeat_{repeat:02d}"
        write_json(
            point / "summary.json",
            {
                "status": "complete",
                "failed_queries": 0,
                "db_qps": 1.25 + repeat,
                "worker_cleanup": {"database_backends_fully_released": True},
                "r0_profile_construction_gate": {
                    "target_resource": "memory",
                    "passed": True,
                },
                "r0_decision_window": {
                    "stable": True,
                    "utilization_pct": {
                        "cpu": cpu,
                        "memory": 86.0 + repeat / 10.0,
                        "io": 43.0 + repeat / 10.0,
                    },
                },
            },
        )
        write_json(
            point / "run_manifest.json",
            {
                "benchmark": "tpch",
                "workload": {
                    "query_files": ["q1.sql", "q2.sql"],
                    "weights": {"q1.sql": 50, "q2.sql": 50},
                    "workers": 36,
                    "seed": 20260722,
                    "measurement_schedule": "balanced_seeded_cycle",
                },
                "endpoint": {
                    "port": 63301,
                    "database": "fullgrid_tpch_sf10_migrated_20260727",
                },
                "container": "pg5_tpch_ap_slot_node01_20260726",
                "timing": {"warmup": 180, "minimum": 180},
                "io_utilization": {
                    "read_cap_mib_s": 16.0,
                    "write_cap_mib_s": 20.0,
                    "total_cap_mib_s": 36.0,
                },
            },
        )
        sample = {
            "cpu_utilization": 0.4,
            "raw_cpu_cores": 7.7 * 0.4,
            "memory_utilization": 0.86,
            "raw_memory_bytes": 2820000000 * 0.86,
            "io_read_cap_bytes_s": 16 * 1024 * 1024,
            "io_write_cap_bytes_s": 20 * 1024 * 1024,
            "io_total_cap_bytes_s": 36 * 1024 * 1024,
        }
        (point / "resource_samples.jsonl").write_text(
            "".join(json.dumps(sample) + "\n" for _ in range(6)),
            encoding="utf-8",
        )
    runtime = contract.ATTEMPT_ROOT / "followup/runtime"
    queries = runtime / "queries"
    queries.mkdir(parents=True)
    workload_runner = runtime / "run_tpch_attempt57_weighted_workload.py"
    point_runner = runtime / "run_tpch_attempt57_calibration_point.sh"
    dependency = runtime / "formal_contracts.py"
    for path, content in (
        (queries / "q1.sql", "SELECT 1;\n"),
        (queries / "q2.sql", "SELECT 2;\n"),
        (workload_runner, "print('runner')\n"),
        (point_runner, "#!/bin/sh\nexit 0\n"),
        (dependency, "VALUE = 1\n"),
    ):
        path.write_text(content, encoding="utf-8")
    return queries, workload_runner, point_runner, dependency


def test_generate_verify_and_tamper() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        queries, workload_runner, point_runner, dependency = build_fixture(
            Path(temporary)
        )
        manifest = contract.generate_manifest(
            output=contract.MANIFEST_PATH,
            workload_runner=workload_runner,
            point_runner=point_runner,
            sql_dir=queries,
            dependencies=[dependency],
        )
        assert len(manifest["strict_r0"]["evidence"]) == 3
        assert len(manifest["calibration"]["probes"]) == 14
        assert manifest["profile"]["cpu_limit_cores"] == 7.7
        assert manifest["profile"]["workers"] == 36
        assert manifest["profile"]["scale_factor"] == 10
        assert manifest["parameters"]["active"] == list(contract.PARAMETERS)
        contract.verify_manifest(contract.MANIFEST_PATH)
        assert contract.generate_manifest(
            output=contract.MANIFEST_PATH,
            workload_runner=workload_runner,
            point_runner=point_runner,
            sql_dir=queries,
            dependencies=[dependency],
        ) == manifest

        manifest_sha256 = contract.sha256_path(contract.MANIFEST_PATH)
        calibration_points = []
        for probe in manifest["calibration"]["probes"]:
            point_root = (
                contract.CALIBRATION_ROOT
                / "points"
                / probe["calibration_id"]
            )
            result_path = point_root / "point_result.json"
            write_json(
                result_path,
                {
                    "status": "complete",
                    "calibration_id": probe["calibration_id"],
                    "manifest_sha256": manifest_sha256,
                    "effective_parameters": probe["params"],
                    "candidate_measurement_protocol": {
                        "stable": True,
                        "correctness_passed": True,
                    },
                    "cleanup": {
                        "database_backends_fully_released": True,
                        "defaults_restored": True,
                        "global_defaults_unchanged": True,
                    },
                    "candidate_parameter_application": {
                        "mode": "transaction_local_set_local",
                        "alter_system_used": False,
                        "global_settings_changed": False,
                        "immediate_readback_required": True,
                        "workers_expected": 36,
                        "workers_verified": 36,
                    },
                },
            )
            calibration_points.append(
                {
                    "status": "usable",
                    "calibration_id": probe["calibration_id"],
                    "manifest_sha256": manifest_sha256,
                    "parameter": probe["parameter"],
                    "direction": probe["direction"],
                    "params": probe["params"],
                    "point_result_path": str(result_path.resolve()),
                    "point_result_sha256": contract.sha256_path(result_path),
                }
            )
        write_json(
            contract.CALIBRATION_SUMMARY,
            {
                "status": "complete",
                "manifest_sha256": manifest_sha256,
                "expected_usable_points": 14,
                "usable_points": 14,
                "points": calibration_points,
            },
        )
        contract.verify_manifest(contract.MANIFEST_PATH, require_calibration=True)
        first_result = Path(calibration_points[0]["point_result_path"])
        first_result.write_text(
            first_result.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        try:
            contract.verify_manifest(
                contract.MANIFEST_PATH,
                require_calibration=True,
            )
        except RuntimeError as exc:
            assert "result SHA256 changed" in str(exc)
        else:
            raise AssertionError("tampered calibration result was accepted")

        tampered = json.loads(contract.MANIFEST_PATH.read_text(encoding="utf-8"))
        tampered["calibration"]["probes"][0]["params"]["seq_page_cost"] = 0.2
        write_json(contract.MANIFEST_PATH, tampered)
        try:
            contract.verify_manifest(contract.MANIFEST_PATH)
        except RuntimeError as exc:
            assert "calibration design" in str(exc)
        else:
            raise AssertionError("tampered calibration probe was accepted")
        write_json(contract.MANIFEST_PATH, manifest)

        summary = contract.ATTEMPT_ROOT / "r0/repeat_01/summary.json"
        summary.write_text(summary.read_text(encoding="utf-8") + " ", encoding="utf-8")
        try:
            contract.verify_manifest(contract.MANIFEST_PATH)
        except RuntimeError as exc:
            assert "changed" in str(exc)
        else:
            raise AssertionError("mutated R0 summary was accepted")


def test_forbidden_lineage_rejected() -> None:
    try:
        contract.reject_forbidden(
            "/tmp/node04/attempt48_global_replay_cap5_20260726",
            field="test",
        )
    except RuntimeError as exc:
        assert "forbidden lineage" in str(exc)
    else:
        raise AssertionError("forbidden lineage was accepted")


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.autocommit = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, params: dict) -> None:
        self.params = params
        self.executed = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, values=None) -> None:
        self.executed.append((str(sql), values))
        if "current_setting" in str(sql):
            self.rows = sorted(self.params.items())

    def fetchall(self):
        return self.rows


def test_worker_transaction_local_application() -> None:
    params = dict(contract.DEFAULTS)
    raw_connection = FakeConnection()
    raw_cursor = FakeCursor(params)
    audit = weighted.WorkerAudit(params)
    cursor = weighted.WorkerCursor(
        raw_cursor,
        raw_connection=raw_connection,
        application_name="resource-adaptive-tpch-worker-0",
        params=params,
        audit=audit,
    )
    with cursor:
        cursor.execute("SELECT 1")
    set_local = [
        sql for sql, _ in raw_cursor.executed if sql.startswith("SET LOCAL ")
    ]
    assert len(set_local) == 7
    assert any("current_setting" in sql for sql, _ in raw_cursor.executed)
    assert raw_connection.commits == 1
    assert raw_connection.rollbacks == 0
    worker_rows = audit.snapshot()
    assert len(worker_rows) == 1
    assert worker_rows[0]["transactions_applied"] == 1
    assert worker_rows[0]["immediate_readback_passed"] is True


def test_point_script_has_no_global_parameter_write() -> None:
    script = (
        Path(__file__).resolve().parent
        / "run_tpch_attempt57_calibration_point.sh"
    ).read_text(encoding="utf-8")
    forbidden = "ALTER" + " SYSTEM"
    assert forbidden.lower() not in script.lower()
    assert "pg_reload_conf" not in script
    assert "--candidate-params-json" in script
    assert "timeout --signal=TERM --kill-after=5s 30s" in script


def test_measurement_boundary_reset_disables_poll_timeout() -> None:
    base_runner = (
        Path(__file__).resolve().parent
        / "run_pg_weighted_sql_baseline_seeded.py"
    ).read_text(encoding="utf-8")
    reset = 'cursor.execute("SELECT pg_stat_statements_reset()")'
    reset_index = base_runner.index(reset)
    before = base_runner[max(0, reset_index - 500):reset_index]
    after = base_runner[reset_index:reset_index + 500]
    assert 'cursor.execute("SET statement_timeout = 0")' in before
    assert 'cursor.execute("SET statement_timeout = \'5s\'")' in after
    cleanup_index = base_runner.index(
        "SELECT pg_cancel_backend(pid)",
        reset_index,
    )
    cleanup_before = base_runner[max(0, cleanup_index - 700):cleanup_index]
    assert 'cursor.execute("SET statement_timeout = 0")' in cleanup_before


def main() -> int:
    test_generate_verify_and_tamper()
    test_forbidden_lineage_rejected()
    test_worker_transaction_local_application()
    test_point_script_has_no_global_parameter_write()
    test_measurement_boundary_reset_disables_poll_timeout()
    print("attempt57 follow-up tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
