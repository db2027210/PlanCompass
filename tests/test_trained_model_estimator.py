from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import unittest

from mysql_cost_adapter import MySQLCostAdapter
from trained_model_estimator import (
    DEFAULT_ARTIFACTS,
    TrainedModelResourceEstimator,
    build_mysql_optimistic_pruning_input,
)


def test_mysql_error_envelope_uses_native_units_and_never_emits_qps(tmp_path) -> None:
    artifact = {
        "schema_version": "mysql_resource_error_bounds_v1",
        "immutable": True,
        "simultaneous_coverage_guaranteed": True,
        "bindings": {
            "freeze_id": "f1", "benchmark_id": "b1", "scenario_id": "s1",
            "resource_envelope_sha256": "a" * 64, "domain_id": "d1",
        },
        "resources": {
            "cpu": {"unit": "cpu_seconds_per_window", "heldout_abs_error_max": 4,
                    "baseline_applied_error_bound": {"lower": 2, "upper": 6}},
            "memory": {"unit": "memory_mb", "heldout_abs_error_max": 256,
                       "baseline_applied_error_bound": 128},
            "io": {"unit": "io_mb_per_window", "heldout_abs_error_max": 10,
                   "baseline_applied_error_bound": 15},
        },
    }
    path = tmp_path / "bounds.json"
    raw = json.dumps(artifact, sort_keys=True).encode()
    path.write_bytes(raw)
    payload = build_mysql_optimistic_pruning_input(
        predicted_resources={"cpu": 2, "memory": 1, "io": 3},
        normalization={"cpu_divisor": 2, "memory_mb_per_unit": 1024, "io_divisor": 5},
        error_bound_artifact_path=path,
    )
    assert payload is not None
    assert payload["asymmetric_resource_errors"]["cpu"]["lower"] == 2
    assert payload["asymmetric_resource_errors"]["cpu"]["upper"] == 3
    assert payload["asymmetric_resource_errors"]["memory"]["lower"] == .25
    assert payload["asymmetric_resource_errors"]["io"]["upper"] == 3
    assert payload["error_bound_provenance"]["artifact_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "qps" not in json.dumps(payload).lower()


def test_mysql_error_envelope_fails_open_on_bad_unit_or_no_joint_coverage(tmp_path) -> None:
    artifact = {
        "schema_version": "mysql_resource_error_bounds_v1", "immutable": True,
        "simultaneous_coverage_guaranteed": False, "bindings": {}, "resources": {},
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(artifact))
    assert build_mysql_optimistic_pruning_input(
        predicted_resources={"cpu": 1, "memory": 1, "io": 1},
        normalization={"cpu_divisor": 1, "memory_mb_per_unit": 1, "io_divisor": 1},
        error_bound_artifact_path=path,
    ) is None


DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("joblib", "numpy", "pandas", "sklearn")
)
ARTIFACTS_AVAILABLE = all(Path(path).is_file() for path in DEFAULT_ARTIFACTS.values())


class ExplainClient:
    def __init__(self) -> None:
        self.explain_calls = 0

    def execute(self, sql: str) -> str:
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            self.explain_calls += 1
            return json.dumps(
                {
                    "query_block": {
                        "ordering_operation": {
                            "using_filesort": True,
                            "table": {
                                "table_name": "lineitem",
                                "access_type": "range",
                                "key": "idx_shipdate",
                                "rows_examined_per_scan": 12000,
                                "rows_produced_per_join": 900,
                                "filtered": "7.5",
                                "index_condition": "l_shipdate >= 1994",
                                "cost_info": {
                                    "read_cost": "450.5",
                                    "eval_cost": "90.0",
                                    "prefix_cost": "540.5",
                                    "data_read_per_join": "18M",
                                },
                            },
                        }
                    }
                }
            ) + "\n"
        if sql.startswith("SELECT COALESCE(SUM(CURRENT_NUMBER_OF_BYTES_USED)"):
            return "1536.0\n"
        return ""


@unittest.skipUnless(
    DEPENDENCIES_AVAILABLE and ARTIFACTS_AVAILABLE,
    "trained model runtime or artifacts are unavailable",
)
class TrainedModelIntegrationTest(unittest.TestCase):
    def test_real_artifacts_drive_one_fresh_explain_prediction(self) -> None:
        client = ExplainClient()
        estimator = TrainedModelResourceEstimator(
            MySQLCostAdapter(client),
            {
                "type": "trained_models",
                "benchmark": "CH",
                "case_number": 21,
                "feature_source": {
                    "queries": [{"sql": "SELECT 1", "weight": 4}],
                    "active_sql_count": 4,
                },
                "memory_context": {"source": "performance_schema"},
                "normalization": {
                    "window_seconds": 5,
                    "cpu_divisor": 5,
                    "memory_mb_per_unit": 1024,
                    "io_divisor": 5,
                },
            },
        )
        resource = estimator(
            None,
            {
                "io_block_read_cost": 1.0,
                "memory_block_read_cost": 0.5,
                "row_evaluate_cost": 0.1,
            },
        )
        self.assertEqual(client.explain_calls, 1)
        self.assertTrue(all(resource[name] >= 0.0 for name in ("cpu", "memory", "io")))
        self.assertEqual(set(resource["_metadata"]["artifacts"]), {"cpu", "memory", "io"})
        self.assertEqual(len(estimator.measurements), 1)


if __name__ == "__main__":
    unittest.main()
