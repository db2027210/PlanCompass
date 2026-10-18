#!/usr/bin/env python3
"""Complete point31's unsealed derived-resource contract without rerunning it."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Sequence


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
ROOT = LANE / "history/job/outer/attempt_4_formal_dbqps_v1_retry3"
WORKSPACE = LANE / "workspace_outer_attempt4_formal_dbqps_v1_retry3"
POINT_ROOT = ROOT / "formal_points/point_0031"
RESULT = POINT_ROOT / "point_result.json"
RESULT_BEFORE = POINT_ROOT / "point_result.before_derived_contract_completion.json"
RAW = POINT_ROOT / "measurement_resource_samples.jsonl"
DERIVED = POINT_ROOT / "measurement_derived_resource_samples.jsonl"
EVIDENCE = ROOT / "point31_derived_contract_completion_evidence.json"
EXPECTED_POINT_ID = "job_experience_table_outer_attempt4_formal_dbqps_v1_retry3_0031"
EXPECTED_RUNNER_SHA = "1743855faee3f0ab1bff2335b3ef975ab7b28bdba0b21c5463ea61339a11bb66"
EXPECTED_ENGINE_SHA = "b3cd68489fdfefc00e11ce569ccfa4e327919f20f0354b74746c44d4f6012430"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lane_run(
    command: Sequence[str],
    *,
    check: bool = True,
    capture: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    argv = list(command)
    if argv and argv[0] == "sudo":
        argv = argv[1:]
    return subprocess.run(
        argv,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=kwargs.get("timeout"),
        cwd=str(kwargs["cwd"]) if kwargs.get("cwd") is not None else None,
        input=kwargs.get("input_text"),
    )


def write_immutable(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite immutable repair artifact: {path}")
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".repair.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"stale repair temporary exists: {temporary}")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> int:
    for path in (
        RESULT,
        RAW,
        WORKSPACE / "formal_pg_point_runner.py",
        WORKSPACE / "formal_pg_engine.py",
    ):
        if not path.is_file():
            raise RuntimeError(f"point31 repair input missing: {path}")
    if sha256(WORKSPACE / "formal_pg_point_runner.py") != EXPECTED_RUNNER_SHA:
        raise RuntimeError("point31 repair runner SHA mismatch")
    if sha256(WORKSPACE / "formal_pg_engine.py") != EXPECTED_ENGINE_SHA:
        raise RuntimeError("point31 repair engine SHA mismatch")
    before_bytes = RESULT.read_bytes()
    result = json.loads(before_bytes)
    measurement = result.get("measurement") or {}
    if (
        result.get("status") != "complete"
        or result.get("point_id") != EXPECTED_POINT_ID
        or measurement.get("correctness_passed") is not True
        or (measurement.get("resource_stability") or {}).get("passed") is not True
        or measurement.get("db_qps_source")
        != "pg_stat_statements_sum_calls_delta_over_formal_measurement_seconds"
        or float(measurement.get("db_qps") or 0.0) <= 0.0
        or result.get("oom_killed") is not False
        or int(result.get("database_restart_count") or 0) != 0
    ):
        raise RuntimeError("point31 is not a valid complete formal measurement")
    if measurement.get("derived_resource_samples_path") or measurement.get(
        "derived_resource_samples_sha256"
    ):
        raise RuntimeError("point31 derived-resource contract is already populated")
    if str(measurement.get("raw_resource_samples_path")) != str(RAW):
        raise RuntimeError("point31 raw resource path mismatch")
    if sha256(RAW) != measurement.get("raw_resource_samples_sha256"):
        raise RuntimeError("point31 raw resource SHA mismatch")

    import sys

    sys.path.insert(0, str(WORKSPACE))
    import p24_pg_recost_runner as p24  # type: ignore

    p24.run = lane_run
    p24.configure_modules("job")
    import formal_pg_point_runner as runner  # type: ignore

    raw_rows = [
        json.loads(line)
        for line in RAW.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    execution = result.get("measurement_execution") or {}
    transaction_path_raw = execution.get("transaction_log_path")
    derived_rows = runner._stability_samples_from_evidence(
        "job",
        "memory",
        RAW,
        Path(str(execution["query_log_path"])),
        Path(str(transaction_path_raw)) if transaction_path_raw else None,
        cutoff_epoch=float(measurement["measurement_cutoff_epoch"]),
    )
    if len(derived_rows) != int(measurement["sample_count"]) or not derived_rows:
        raise RuntimeError(
            f"unexpected derived row count: {len(derived_rows)} from {len(raw_rows)}"
        )
    derived_payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in derived_rows
    ).encode("utf-8")
    write_immutable(DERIVED, derived_payload)

    derived_means = {
        resource: sum(float(row[f"{resource}_utilization"]) for row in derived_rows)
        / len(derived_rows)
        for resource in ("cpu", "memory", "io")
    }
    recorded_means = measurement.get("resource_mean_utilization") or {}
    for resource, derived_mean in derived_means.items():
        if abs(derived_mean - float(recorded_means[resource])) > 1e-12:
            raise RuntimeError(
                f"point31 derived {resource} mean differs from original summary: "
                f"{derived_mean} != {recorded_means[resource]}"
            )

    write_immutable(RESULT_BEFORE, before_bytes)
    before_sha = sha256(RESULT_BEFORE)
    measurement["derived_resource_samples_path"] = str(DERIVED)
    measurement["derived_resource_samples_sha256"] = sha256(DERIVED)
    result["measurement"] = measurement
    atomic_json(RESULT, result)
    after_sha = sha256(RESULT)
    evidence = {
        "schema_version": 1,
        "audit_id": "pg5_job_attempt4_point31_derived_contract_completion_v1",
        "repair_scope": "add_missing_derived_resource_path_and_sha_only",
        "raw_measurement_unchanged": True,
        "original_measurement_summary_values_unchanged": True,
        "formal_point_rerun": False,
        "candidate_budget_consumed": False,
        "point_id": EXPECTED_POINT_ID,
        "point_result_before_path": str(RESULT_BEFORE),
        "point_result_before_sha256": before_sha,
        "point_result_completed_path": str(RESULT),
        "point_result_completed_sha256": after_sha,
        "raw_resource_samples_path": str(RAW),
        "raw_resource_samples_sha256": sha256(RAW),
        "raw_sample_count": len(raw_rows),
        "derived_resource_samples_path": str(DERIVED),
        "derived_resource_samples_sha256": sha256(DERIVED),
        "derived_sample_count": len(derived_rows),
        "derived_resource_means": derived_means,
        "original_resource_means": recorded_means,
        "derivation_function": (
            "formal_pg_point_runner._stability_samples_from_evidence"
        ),
        "formal_runner_sha256": EXPECTED_RUNNER_SHA,
        "formal_engine_sha256": EXPECTED_ENGINE_SHA,
        "repair_script_path": str(Path(__file__).resolve()),
        "repair_script_sha256": sha256(Path(__file__).resolve()),
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    atomic_json(EVIDENCE, evidence)
    print(
        json.dumps(
            {
                **evidence,
                "evidence_path": str(EVIDENCE),
                "evidence_sha256": sha256(EVIDENCE),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
