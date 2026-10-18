#!/usr/bin/env python3
"""Publish the JOB mysql-only, history-first OAT v2 contract."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_job_oat_calibration_20260723_v2_mysql_only"
FREEZE = "frozen_mysql_job_oat_calibration_20260723_v2_mysql_only"
V1_BUILDER = ROOT / "shell/build_mysql_job_oat_calibration_contract.py"


class ContractError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pointer(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ContractError(f"missing/unsafe historical evidence: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_v1_builder():
    spec = importlib.util.spec_from_file_location("job_oat_v1_builder", V1_BUILDER)
    if spec is None or spec.loader is None:
        raise ContractError("cannot load v1 builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def history_provenance() -> dict[str, object]:
    random_walk = ROOT / "history/parallel24_20260721_v1/mysql/random_walk/job/points/baseline_search/attempt_04"
    recost = ROOT / "history/parallel24_20260721_v1/mysql/recost_gradient_descent/parallel24_job_memory_r1/round_000_baseline/attempt_01"
    preflight = ROOT / "preflight/full_20260721"
    measurements = ROOT / "history/mysql/job_measurements"
    candidates = [
        {
            "candidate_id": "parallel24_random_walk_job_attempt_04",
            "artifacts": [pointer(random_walk / name) for name in (
                "point_start.json", "point_result.json", "cost_tables_after_measurement.json",
                "database_samples.jsonl", "resource_samples.jsonl", "correctness.json")],
            "accepted_for_T": False,
            "reasons": [
                "resource envelope is 8 CPU/2 GiB/367001600 Bps, not the frozen mysql-only JOB envelope",
                "old run status is rejected and it supplies no independent lower/upper contrasts",
            ],
        },
        {
            "candidate_id": "parallel24_recost_job_baseline",
            "artifacts": [pointer(recost / name) for name in (
                "point_start.json", "point_result.json", "cost_tables_after_measurement.json",
                "database_samples.jsonl", "resource_samples.jsonl", "correctness.json")],
            "accepted_for_T": False,
            "reasons": [
                "resource envelope is 8 CPU/2 GiB/367001600 Bps, not the frozen mysql-only JOB envelope",
                "baseline alone cannot identify four parameter effects and the resource-only rolling window is unstable",
            ],
        },
        {
            "candidate_id": "preflight_full_job_joint_grid",
            "artifacts": [pointer(preflight / "job.json"), pointer(preflight / "job_plan_audit.json")],
            "accepted_for_T": False,
            "reasons": [
                "resource capacity is 2 CPU/6 GiB/100 MBps rather than the frozen JOB envelope",
                "QPS is client-side and therefore forbidden for formal DBQPS",
                "no exact requested/effective/readback optimizer-cost triplet or formal envelope hashes",
                "plan audit is reduced structural data, not the complete strict raw EXPLAIN bundle required for model inference",
            ],
        },
        {
            "candidate_id": "legacy_job_measurements",
            "artifacts": [pointer(path) for path in sorted(measurements.glob("*.json"))],
            "accepted_for_T": False,
            "reasons": [
                "client-QPS-only artifacts lack scenario, resource values, envelope, cost readback and valid-run proof",
            ],
        },
    ]
    return {
        "schema_version": "mysql_job_history_first_provenance_v1",
        "formal_scope": "mysql_only",
        "engine": "mysql",
        "scenario": "job_memory",
        "cutoff": {"inclusive": True, "local_time": "2026-07-21T00:00:00+08:00", "timezone": "Asia/Shanghai"},
        "evidence_order": [
            "historical real MySQL replay with verifiable DBQPS/resources/cost readback/envelope/valid run",
            "full-rank log-parameter regression with held-out residual envelope",
            "strict raw EXPLAIN physical features through a frozen in-domain MySQL resource model",
            "minimal real OAT only for remaining unsupported parameter/resource effects",
        ],
        "forbidden_inputs": ["PostgreSQL results", "client QPS", "cluster count without strict raw physical features"],
        "historical_candidates": candidates,
        "historical_valid_point_count": 0,
        "historical_T_created": False,
        "frozen_job_model_route": {
            "available": False,
            "reason": "trained_model_estimator supports only H/TPCH, C/TPCC and CH/HYBRID; JOB is out of domain",
            "fail_open_to_real_replay": True,
            "unnecessary_treatment_explain_capture_suppressed": 72,
        },
        "remaining_gap": {
            "active_parameter_count": 4,
            "anchor_plus_lower_upper_treatments_per_repeat": 9,
            "independent_repeat_count": 3,
            "minimum_distinct_formal_points": 27,
            "reason": "three independent contrasts for each side of each parameter; technical failures do not count",
        },
        "status": "NO_ELIGIBLE_HISTORY_MINIMAL_OAT_REQUIRED",
    }


def build() -> dict[str, bytes]:
    base = load_v1_builder().build()
    provenance = history_provenance()
    data: dict[str, bytes] = {"job.historical_provenance.json": canonical(provenance)}

    workload = json.loads(base["job.workload_binding.json"])
    workload["schema_version"] = "mysql_job_oat_workload_binding_v2"
    workload["formal_scope"] = "mysql_only"
    data["job.workload_binding.json"] = canonical(workload)

    space = json.loads(base["job.parameter_space.json"])
    space.update({
        "schema_version": "mysql_job_oat_parameter_space_v2",
        "freeze_id": FREEZE,
        "formal_scope": "mysql_only",
        "trained_estimator_job_route_available": False,
        "trained_estimator_predictions_used_for_oat": False,
    })
    data["job.parameter_space.json"] = canonical(space)

    request = json.loads(base["job.oat_calibration_request.json"])
    request["schema_version"] = "mysql_t_calibration_request_v1"
    request["binding"]["version"] = "mysql_job_T_history_first_real_oat_v2"
    request["binding"]["parameter_space"].update({
        "version": FREEZE,
        "artifact": {"path": "job.parameter_space.json", "sha256": hashlib.sha256(data["job.parameter_space.json"]).hexdigest()},
    })
    request["binding"]["workload"]["artifact"] = {
        "path": "job.workload_binding.json", "sha256": hashlib.sha256(data["job.workload_binding.json"]).hexdigest()}
    request["evidence_policy"] = {
        "historical_first": True,
        "historical_provenance": {"path": "job.historical_provenance.json", "sha256": hashlib.sha256(data["job.historical_provenance.json"]).hexdigest()},
        "postgresql_evidence_forbidden": True,
        "client_qps_forbidden": True,
        "technical_failures_count_as_valid_runs": False,
        "missing_model_domain_fails_open_to_real_oat": True,
    }
    data["job.oat_calibration_request.json"] = canonical(request)

    gates = {
        "mysql_resource_envelope_readback_verified": False,
        "host_admission_passed": False,
        "static_cpu_nonoverlap": False,
        "static_numa_nonoverlap": False,
        "static_memory_swap_isolation": False,
        "static_physical_disk_nonoverlap": False,
        "static_process_isolation": False,
        "latest_six_30s_bidirectional_dynamic_non_interference": False,
        "host02_job_xp_mutex_zero_pressure": False,
        "exclusive_lane_lease_active": False,
        "session_owned_fresh_work_copy_verified": False,
    }
    controller = json.loads(base["job.oat_controller_plan.json"])
    controller.update({
        "schema_version": "mysql_job_oat_controller_plan_v2",
        "freeze_id": FREEZE,
        "formal_scope": "mysql_only",
        "status": "HISTORY_AUDITED_MINIMAL_OAT_BLOCKED_ON_RUNTIME_GATES",
        "runtime_gates": gates,
    })
    controller["design"]["history_first"] = True
    controller["design"]["historical_valid_point_count"] = 0
    controller["design"]["model_inference_used"] = False
    controller["design"]["model_inference_rejection"] = "JOB model route absent"
    controller["design"]["gap_point_count"] = 27
    controller["measurement"]["window_numbering_starts_at"] = 1
    controller["measurement"]["decision_rule"] = "at 180s inspect windows 1-6; if unstable advance by one 30s window through 600s"
    controller["measurement"]["qps_stability_required"] = False
    data["job.oat_controller_plan.json"] = canonical(controller)

    runtime = json.loads(base["job.runtime.template.json"])
    runtime.update({
        "schema_version": "mysql_job_oat_runtime_template_v2",
        "freeze_id": FREEZE,
        "formal_scope": "mysql_only",
        "runtime_gates": gates,
        "postgresql_counterpart_required": False,
        "cross_engine_envelope_equality_required": False,
    })
    runtime["request"] = {"path": "job.oat_calibration_request.json", "sha256": hashlib.sha256(data["job.oat_calibration_request.json"]).hexdigest()}
    runtime["controller_plan"] = {"path": "job.oat_controller_plan.json", "sha256": hashlib.sha256(data["job.oat_controller_plan.json"]).hexdigest()}
    data["job.runtime.template.json"] = canonical(runtime)

    manifest = json.loads(base["manifest.json"])
    manifest.update({
        "schema_version": "mysql_job_oat_contract_manifest_v2",
        "freeze_id": FREEZE,
        "formal_scope": "mysql_only",
        "status": "HISTORY_AUDITED_MINIMAL_OAT_NOT_STARTED",
        "history_first": True,
        "historical_valid_point_count": 0,
        "model_inference_used": False,
        "minimum_distinct_formal_points": 27,
        "postgresql_counterpart_required": False,
        "cross_engine_envelope_equality_required": False,
    })
    manifest["published_checksums"] = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(data.items())}
    data["manifest.json"] = canonical(manifest)
    data["checksums.sha256"] = "".join(
        f"{hashlib.sha256(data[name]).hexdigest()}  {name}\n" for name in sorted(data)
    ).encode()
    return data


def publish(data: dict[str, bytes]) -> None:
    if OUT.exists():
        if (not OUT.is_dir() or set(path.name for path in OUT.iterdir()) != set(data)
                or any((OUT / name).read_bytes() != content for name, content in data.items())):
            raise ContractError("existing immutable v2 contract differs")
        return
    temporary = Path(tempfile.mkdtemp(prefix=f".{OUT.name}.", dir=OUT.parent))
    try:
        for name, content in data.items():
            path = temporary / name
            path.write_bytes(content)
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        os.replace(temporary, OUT)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    data = build()
    already = OUT.exists()
    if args.execute:
        publish(data)
    print(json.dumps({
        "status": "PASS", "mode": "execute" if args.execute else "dry-run",
        "output": str(OUT.relative_to(ROOT)), "already_published": already,
        "formal_scope": "mysql_only", "historical_valid_point_count": 0,
        "model_route_available": False, "minimum_oat_gap_points": 27,
        "oat_started": False, "t_created": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, sort_keys=True))
        raise SystemExit(2)
