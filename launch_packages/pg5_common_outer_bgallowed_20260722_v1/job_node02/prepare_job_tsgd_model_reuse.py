#!/usr/bin/env python3
"""Seal p31-calibrated resource models for the fresh JOB T-SGD generation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


SOURCE_CONFIG_SHA256 = "506da7a99c160f8dd4ed41f0c62cb8171c77486e870a7bbf9eb67095fef0a4a0"
EXPECTED_MODEL_SHA256 = {
    "cpu_resource_models.json": "23ccf93ec42b85925db8367957d9066dbe553129c69a5c60a3c2185981e7f306",
    "io_resource_models.json": "4706567aeffd8e3d80935d1169d2c2807f38c31bfa615e07e0e0216655224257",
    "memory_resource_models.json": "85188f9c936b3d989adab751e36bd3d8237a104d082199e2dedd05fb1812b23c",
    "resource_model_retrain_summary.json": "63553471142b82bb1f6206618ac59d30eeef13ab41c0df5d6af0841178fe3dfa",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_new(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--source-model-dir", type=Path, required=True)
    parser.add_argument("--new-attempt", type=Path, required=True)
    parser.add_argument("--config-output", type=Path, required=True)
    args = parser.parse_args()
    source_config = args.source_config.resolve()
    source_model_dir = args.source_model_dir.resolve()
    attempt = args.new_attempt.resolve()
    config_output = args.config_output.resolve()
    if sha256(source_config) != SOURCE_CONFIG_SHA256:
        raise RuntimeError("T-SGD generation source config SHA mismatch")
    for name, expected in EXPECTED_MODEL_SHA256.items():
        actual = sha256(source_model_dir / name)
        if actual != expected:
            raise RuntimeError(f"sealed source model SHA mismatch: {name}: {actual} != {expected}")

    bundle = attempt / "sealed_resource_models_p31_v1"
    if bundle.exists() or bundle.is_symlink():
        raise RuntimeError(f"sealed model bundle already exists: {bundle}")
    bundle.mkdir(parents=True, exist_ok=False)
    for name in EXPECTED_MODEL_SHA256:
        destination = bundle / name
        shutil.copyfile(source_model_dir / name, destination)
        with destination.open("rb") as handle:
            os.fsync(handle.fileno())
        destination.chmod(0o444)
        if sha256(destination) != EXPECTED_MODEL_SHA256[name]:
            raise RuntimeError(f"copied model SHA mismatch: {name}")

    config = json.loads(source_config.read_text(encoding="utf-8"))
    estimator = config["metrics"]["plan_estimator"]
    for resource in ("cpu", "io", "memory"):
        estimator[resource]["learned_model"]["path"] = str(
            bundle / f"{resource}_resource_models.json"
        )
        estimator[resource]["learned_model"]["enabled"] = True
        estimator[resource]["learned_model"]["required"] = True
    retraining = estimator["model_retraining"]
    retraining["enabled"] = False
    retraining["apply_to_runtime"] = False
    retraining["reuse_reason"] = (
        "Exact p31 startup baseline and hardware/profile are unchanged; reuse its "
        "sealed calibrated models and held-out error envelopes without importing "
        "any grid candidate ranking or acceptance."
    )
    retraining["sealed_reuse_bundle"] = {
        "path": str(bundle),
        "model_sha256": copy.deepcopy(EXPECTED_MODEL_SHA256),
        "baseline_point_role": "immutable_startup_baseline_p31",
        "legacy_grid_candidate_role": "not_used",
    }
    generation = config.setdefault("_tsgd_generation", {})
    generation["resource_model_source"] = "sealed_p31_calibrated_bundle"
    generation["resource_model_sha256"] = copy.deepcopy(EXPECTED_MODEL_SHA256)
    generation["failed_fresh_retrain_history_role"] = "preflight_audit_only"
    write_json_new(config_output, config)
    audit = {
        "schema_version": 1,
        "status": "sealed_model_reuse_ready",
        "config_path": str(config_output),
        "config_sha256": sha256(config_output),
        "bundle_path": str(bundle),
        "model_sha256": EXPECTED_MODEL_SHA256,
        "baseline_point": 31,
        "baseline_point_is_same_as_tsgd_startup_reuse": True,
        "imports_grid_candidate_ranking": False,
        "imports_grid_acceptance_or_shrink": False,
    }
    write_json_new(attempt / "tsgd_model_reuse_seal.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
