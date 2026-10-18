import hashlib
import json

import pytest

from three_level_fingerprint_validator import validate_three_level_candidate


def _sha(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _candidate():
    rows = [
        {
            "queryid": "q1",
            "fingerprint": "exact-structure-sha256:" + "1" * 64,
        }
    ]
    plan = "ratio-plan-sha256:" + _sha(rows)
    feature = {"cpu": 1.0, "memory": 1.005, "io": 0.995}
    resource = {"cpu": 4.0, "memory": 8.04, "io": 1.99}
    feature_level = {"cpu": 200, "memory": 201, "io": 199}
    utilization_level = {"cpu": 160, "memory": 161, "io": 159}
    fingerprint_payload = {
        "plan": plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": utilization_level,
    }
    return {
        "feature_ratio": feature,
        "predicted_resource": resource,
        "workload_plan_fingerprint": plan,
        "fingerprint_validation": {
            "resource_estimation_mode": "ratio_scale",
            "cost_parameter_free_features": True,
            "qps_used_for_inner_scoring": False,
            "workload_plan_fingerprint": plan,
            "expected_plannable_query_count": 1,
            "query_features": rows,
            "feature_ratio": feature,
            "baseline_real_resource": {"cpu": 4.0, "memory": 8.0, "io": 2.0},
        },
        "three_level_fingerprint": {
            "contract": "plan_feature_resource_three_level_v1",
            "valid": True,
            "parameter_fallback_used": False,
            "plan_level": plan,
            "feature_ratio_level": feature_level,
            "predicted_resource_level": utilization_level,
            "id": "three-level-sha256:" + _sha(fingerprint_payload),
        },
    }


def test_recomputes_complete_fingerprint():
    result = validate_three_level_candidate(
        _candidate(),
        {"cpu": 5.0, "memory": 10.0, "io": 2.5},
    )
    assert result["valid"] is True
    assert result["query_count"] == 1


def test_rejects_metadata_digest_mismatch():
    candidate = _candidate()
    candidate["fingerprint_validation"]["query_features"][0]["fingerprint"] = (
        "exact-structure-sha256:" + "2" * 64
    )
    with pytest.raises(ValueError, match="ratio-plan"):
        validate_three_level_candidate(
            candidate,
            {"cpu": 5.0, "memory": 10.0, "io": 2.5},
        )
