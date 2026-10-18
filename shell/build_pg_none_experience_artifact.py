#!/usr/bin/env python3
"""Release an all-resource no-bottleneck predictor from a native XP artifact."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.run_pg_algorithm_c_scene import (
    ACTIVE_PARAMS,
    canonical_sha256,
    validate_experience_artifact,
)


NONE_ADMISSION_CONTRACT = "all_resource_holdout_mae_no_bottleneck_v1"
MAXIMUM_ALL_RESOURCE_MAE_LOG_RATIO = math.log(1.25)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_native_xp_x1x1_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    algorithm = config.get("algorithm_c")
    algorithm = algorithm if isinstance(algorithm, Mapping) else {}
    contract = algorithm.get("workload_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    native = contract.get("native_xp")
    native = native if isinstance(native, Mapping) else {}
    if str(contract.get("benchmark") or "") != "hybench_xp":
        raise RuntimeError("source config is not native HyBench XP")
    if int(native.get("xtpclient") or 0) != 1 or int(native.get("xapclient") or 0) != 1:
        raise RuntimeError("source config is not the required XP x1/x1 workload")
    if native.get("forbidden_query_selection_fields_absent") is not True:
        raise RuntimeError("source config does not forbid XP query selection")
    percentages = native.get("at_percentages")
    expected_percentages = {
        "at1_percent": 40,
        "at2_percent": 30,
        "at3_percent": 15,
        "at4_percent": 5,
        "at5_percent": 7,
        "at6_percent": 3,
    }
    if percentages != expected_percentages:
        raise RuntimeError("source config XP AT mix mismatch")
    for path_key, sha_key in (
        ("native_jar_file", "native_jar_sha256"),
        ("statement_file", "statement_file_sha256"),
    ):
        path = Path(str(native.get(path_key) or ""))
        expected_sha = str(native.get(sha_key) or "")
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise RuntimeError(f"native XP identity mismatch: {path_key}")
    return copy.deepcopy(dict(contract))


def verify_source_artifact(source: Mapping[str, Any]) -> str:
    """Verify source integrity and all fields reused by the none release."""
    source_target = str(source.get("target_resource") or "")
    if source_target not in {"cpu", "memory", "io"}:
        raise RuntimeError("source artifact must have one measured resource target")
    payload = copy.deepcopy(dict(source))
    expected_artifact_sha = str(payload.pop("artifact_sha256", ""))
    if not expected_artifact_sha or canonical_sha256(payload) != expected_artifact_sha:
        raise RuntimeError("source artifact SHA mismatch")
    table = source.get("table")
    if not isinstance(table, Mapping) or set(table) != set(ACTIVE_PARAMS):
        raise RuntimeError("source artifact active parameter table mismatch")
    if canonical_sha256(table) != str(source.get("table_sha256") or ""):
        raise RuntimeError("source artifact table SHA mismatch")
    admission = source.get("formal_admission")
    if not isinstance(admission, Mapping) or admission.get("ready") is not True:
        raise RuntimeError("source artifact is not formally admitted")
    fit = source.get("fit")
    fit = fit if isinstance(fit, Mapping) else {}
    if int(fit.get("train_count") or 0) < 10 or int(fit.get("holdout_count") or 0) < 5:
        raise RuntimeError("source artifact has insufficient train/holdout rows")
    calibration = source.get("resource_prediction_calibration")
    calibration = calibration if isinstance(calibration, Mapping) else {}
    if calibration.get("qps_used") is not False:
        raise RuntimeError("source artifact resource prediction used QPS")
    resources = calibration.get("resources")
    resources = resources if isinstance(resources, Mapping) else {}
    for resource in ("cpu", "memory", "io"):
        row = resources.get(resource)
        row = row if isinstance(row, Mapping) else {}
        coefficients = row.get("parameter_coefficients")
        if (
            str(row.get("family") or "") not in {"raw_plan", "parameter", "hybrid"}
            or not isinstance(coefficients, Mapping)
            or set(coefficients) != set(ACTIVE_PARAMS)
            or row.get("qps_used") is not False
            or row.get("zero_intercept") is not True
        ):
            raise RuntimeError(f"source artifact calibration invalid: {resource}")
    return source_target


def build_none_artifact(
    source: Mapping[str, Any],
    *,
    source_path: Path,
    source_config_path: Path,
    workload_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    source_target = verify_source_artifact(source)
    fit = source.get("fit")
    fit = fit if isinstance(fit, Mapping) else {}
    holdout = source.get("holdout")
    holdout = holdout if isinstance(holdout, Mapping) else {}
    metrics = holdout.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    observed_mae: Dict[str, float] = {}
    for resource in ("cpu", "memory", "io"):
        row = metrics.get(resource)
        row = row if isinstance(row, Mapping) else {}
        mae = float(row.get("mae_log_ratio") or math.inf)
        if not math.isfinite(mae) or mae > MAXIMUM_ALL_RESOURCE_MAE_LOG_RATIO:
            raise RuntimeError(
                f"source {resource} holdout MAE is not admissible: {mae}"
            )
        observed_mae[resource] = mae

    result = copy.deepcopy(dict(source))
    result["target_resource"] = "none"
    result["table_id"] = f"{source.get('table_id') or 'experience_table'}_none_xp_x1x1_v1"
    result["scope"] = "native_hybench_xp_x1x1_all_resources_no_bottleneck"
    result["direction_semantics"] = (
        "all resources are non-bottleneck scoring dimensions; no synthetic "
        "target-resource direction is admitted"
    )
    result["formal_admission"] = {
        "ready": True,
        "errors": [],
        "none_target_admission_contract": NONE_ADMISSION_CONTRACT,
        "admission_predictor_contract": source.get(
            "resource_prediction_calibration", {}
        ).get("contract"),
        "minimum_train_count": 10,
        "minimum_holdout_count": 5,
        "required_training_design_rank": 7,
        "maximum_training_design_condition_number": 1000.0,
        "maximum_all_resource_mae_log_ratio": (
            MAXIMUM_ALL_RESOURCE_MAE_LOG_RATIO
        ),
        "observed_all_resource_mae_log_ratio": observed_mae,
        "observed_training_design": copy.deepcopy(
            (source.get("formal_admission") or {}).get(
                "observed_training_design"
            )
        ),
        "train_count": int(fit.get("train_count") or 0),
        "holdout_count": int(fit.get("holdout_count") or 0),
        "source_target_direction_reused": False,
        "qps_used_for_prediction_or_inner_scoring": False,
    }
    result["none_target_release"] = {
        "contract": "native_hybench_xp_x1x1_none_artifact_release_v1",
        "source_artifact_path": str(source_path.resolve()),
        "source_artifact_sha256": sha256_file(source_path),
        "source_config_path": str(source_config_path.resolve()),
        "source_config_sha256": sha256_file(source_config_path),
        "workload_contract": copy.deepcopy(dict(workload_contract)),
        "workload_contract_sha256": canonical_sha256(workload_contract),
        "all_resource_holdout_mae_log_ratio": observed_mae,
        "qps_used": False,
    }
    result.pop("artifact_sha256", None)
    result["artifact_sha256"] = canonical_sha256(result)
    validate_experience_artifact(result, "none")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source_path = args.source.resolve()
    source_config_path = args.source_config.resolve()
    workload_contract = verify_native_xp_x1x1_config(
        read_json(source_config_path)
    )
    result = build_none_artifact(
        read_json(source_path),
        source_path=source_path,
        source_config_path=source_config_path,
        workload_contract=workload_contract,
    )
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "artifact_sha256": result["artifact_sha256"],
                "all_resource_holdout_mae_log_ratio": result[
                    "formal_admission"
                ]["observed_all_resource_mae_log_ratio"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
