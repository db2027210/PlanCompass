import importlib.util
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "shell/build_ratio_scale_resource_calibration.py"
)
SPEC = importlib.util.spec_from_file_location("calibration_builder", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _rows():
    rows = []
    for index in range(12):
        raw = 0.8 + index * 0.04
        rows.append(
            {
                "cost_parameter_free_features": True,
                "qps_used": False,
                "raw_feature_ratio": {
                    "cpu": raw,
                    "memory": raw,
                    "io": raw,
                },
                "baseline_real_resource": {
                    "cpu": 10.0,
                    "memory": 20.0,
                    "io": 30.0,
                },
                "actual_resource": {
                    "cpu": 10.0 * raw ** 0.5,
                    "memory": 20.0 * raw ** 0.5,
                    "io": 30.0 * raw ** 0.5,
                },
                "db_qps": index * 1e9,
            }
        )
    return rows


def test_builder_recovers_log_ratio_and_ignores_qps():
    artifact = MODULE.build_artifact(
        _rows(),
        source_sha256="a" * 64,
    )
    assert artifact["qps_used"] is False
    for resource in MODULE.RESOURCES:
        assert artifact["resources"][resource]["beta"] == pytest.approx(0.5)
        assert artifact["resources"][resource]["sample_count"] == 12


def test_builder_rejects_declared_qps_use():
    rows = _rows()
    rows[0]["qps_used"] = True
    with pytest.raises(RuntimeError, match="QPS"):
        MODULE.build_artifact(rows, source_sha256="a" * 64)
