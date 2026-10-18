import hashlib
import json
import math

import pytest

from ratio_scale_resource_estimator import RatioScaleResourceEstimator


def _artifact(tmp_path, *, qps_used=False):
    payload = {
        "schema_version": 1,
        "contract": "resource_ratio_log_calibration_v1",
        "qps_used": qps_used,
        "cost_parameter_free_features": True,
        "resources": {
            name: {
                "alpha": 0.0,
                "beta": 0.5,
                "sample_count": 12,
                "loo_absolute_error_p90_pp": 2.0,
            }
            for name in ("cpu", "memory", "io")
        },
    }
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _estimator(path, sha):
    return RatioScaleResourceEstimator(
        pg_host="unused",
        pg_port="5432",
        pg_user="unused",
        pg_dbname="unused",
        estimator_cfg={
            "ratio_scale": {
                "resource_ratio_calibration": {
                    "enabled": True,
                    "artifact_path": str(path),
                    "artifact_sha256": sha,
                }
            }
        },
        inner_estimator=object(),
    )


def test_calibration_is_sha_pinned_and_qps_free(tmp_path) -> None:
    path, sha = _artifact(tmp_path)
    estimator = _estimator(path, sha)
    assert estimator.resource_ratio_calibration["qps_used"] is False
    with pytest.raises(RuntimeError):
        _estimator(path, "0" * 64)


def test_qps_using_artifact_is_rejected(tmp_path) -> None:
    path, sha = _artifact(tmp_path, qps_used=True)
    with pytest.raises(RuntimeError):
        _estimator(path, sha)


def test_calibrated_feature_ratio_still_scales_real_baseline(tmp_path) -> None:
    path, sha = _artifact(tmp_path)
    estimator = _estimator(path, sha)
    estimator._calibrated = True
    estimator._baseline_features = {
        "cpu_total": 1.0,
        "memory_total": 1.0,
        "io_total": 1.0,
        "workload_plan_fingerprint": "ratio-plan-sha256:" + "a" * 64,
    }
    estimator._baseline_real = {"cpu": 10.0, "memory": 20.0, "io": 30.0}
    estimator._baseline_params = {"seq_page_cost": 1.0}
    estimator._baseline_memory_fixed = 0.0
    estimator._baseline_memory_dynamic = 20.0
    estimator._normalize_queries = lambda _sql: []
    estimator._normalize_params = lambda params: dict(params)
    estimator._compute_structural_features = lambda _queries, _params: {
        "cpu_total": 2.0,
        "memory_total": 2.0,
        "io_total": 2.0,
        "workload_plan_fingerprint": "ratio-plan-sha256:" + "b" * 64,
        "query_features": [],
    }

    resource, _, metadata = estimator.estimate_with_metadata(
        [],
        {"seq_page_cost": 2.0},
    )
    expected_ratio = math.sqrt(2.0)
    assert resource["cpu"] == pytest.approx(10.0 * expected_ratio)
    assert resource["memory"] == pytest.approx(20.0 * expected_ratio)
    assert resource["io"] == pytest.approx(30.0 * expected_ratio)
    assert metadata["feature_ratios"]["cpu"] == pytest.approx(expected_ratio)
    assert metadata["precalibration_feature_ratios"]["cpu"] == 2.0
