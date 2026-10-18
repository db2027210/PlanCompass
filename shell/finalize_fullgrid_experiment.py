#!/usr/bin/env python3
"""Backfill EXPLAIN, export, and verify one completed full-real grid search."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Set, Tuple


ROOT = Path(__file__).resolve().parent
BACKFILL = ROOT / "backfill_round_explains.py"
EXPORTER = ROOT / "export_bruteforce_experiment_log.py"
DEFAULT_VERIFIER = ROOT / "verify_structured_fullgrid_log.py"

SEMANTIC_ERROR_EXACT = {
    "result.bad_config_not_false",
    "result.db_qps_did_not_increase",
    "result.none_not_all_three_resources_up",
    "result.none_no_resource_increased",
    "result.final_params_not_exactly_seven_cost_params",
}
SEMANTIC_ERROR_PARTS = (
    "_bottleneck_did_not_drop",
    "_drop_too_extreme_",
)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def scan_authoritative_log(path: Path) -> Tuple[bool, Set[str]]:
    search_complete = False
    backends: Set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            if value.get("event") == "search_complete":
                search_complete = True
            if (
                value.get("event") == "candidate_validation"
                and value.get("status") == "ok"
                and value.get("validation_backend")
            ):
                backends.add(str(value["validation_backend"]))
    return search_complete, backends


def explain_round_coverage(path: Path) -> Tuple[List[int], List[int]]:
    """Return completed round ids and ids already carrying real EXPLAIN samples."""
    latest_summaries: Dict[int, Dict[str, Any]] = {}
    backfilled: Set[int] = set()
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            value = json.loads(raw)
            if not isinstance(value, dict):
                continue
            try:
                round_id = int(value.get("round"))
            except (TypeError, ValueError):
                continue
            if value.get("event") == "round_summary":
                latest_summaries[round_id] = value
            elif value.get("event") == "round_explain_backfill" and value.get("explain_samples"):
                backfilled.add(round_id)
    covered = sorted(
        round_id
        for round_id, summary in latest_summaries.items()
        if summary.get("explain_samples") or round_id in backfilled
    )
    return sorted(latest_summaries), covered


def run(command: List[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)


def require_ok(proc: subprocess.CompletedProcess[str], context: str) -> str:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{context} failed rc={proc.returncode}: {detail}")
    return (proc.stdout or "").strip()


def last_json_object(text: str) -> Dict[str, Any]:
    for raw in reversed(text.splitlines()):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("command output contains no JSON object")


def semantic_only(errors: List[str]) -> bool:
    if not errors:
        return False
    for error in errors:
        if error in SEMANTIC_ERROR_EXACT:
            continue
        if any(part in error for part in SEMANTIC_ERROR_PARTS):
            continue
        return False
    return True


def classify_verification(
    proc: subprocess.CompletedProcess[str],
) -> Tuple[str, int, Dict[str, Any]]:
    """Classify verifier output while preserving semantic misses as usable artifacts."""
    payload = last_json_object((proc.stdout or "") + "\n" + (proc.stderr or ""))
    errors = [str(value) for value in (payload.get("errors") or [])]
    if proc.returncode == 0:
        return "verified", 0, payload
    if semantic_only(errors):
        return "semantic_miss", 10, payload
    return "structural_failure", 1, payload


def logical_alias_path(artifact: Path, logical_exp_id: str) -> Path:
    """Return a deterministic sibling path for one logical alias artifact."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", logical_exp_id):
        raise ValueError(f"invalid logical exp id: {logical_exp_id!r}")
    return artifact.with_name(f"{artifact.stem}.{logical_exp_id}{artifact.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--verifier", type=Path, default=DEFAULT_VERIFIER)
    parser.add_argument("--expected-candidates-per-round", type=int, default=128)
    parser.add_argument("--min-rounds", type=int, default=6)
    parser.add_argument("--max-bottleneck-drop-pct", type=float, default=30.0)
    parser.add_argument("--max-explain-queries", type=int, default=3)
    parser.add_argument(
        "--logical-exp-id",
        action="append",
        default=[],
        help="Export a verified logical alias that reuses this physical run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    log_path = args.log.resolve()
    config = read_json(config_path)
    search_complete, backends = scan_authoritative_log(log_path)
    if not search_complete:
        raise RuntimeError("refusing to finalize: search_complete is missing")

    tuning = config.get("binary_plan_tuning") if isinstance(config.get("binary_plan_tuning"), dict) else {}
    meta = tuning.get("experiment_meta") if isinstance(tuning.get("experiment_meta"), dict) else {}
    machine = meta.get("machine_spec") if isinstance(meta.get("machine_spec"), dict) else {}
    required_meta = {
        "exp_id", "benchmark", "db", "method", "bottleneck",
        "bottleneck_target", "repeat_id", "machine_spec",
    }
    missing_meta = sorted(required_meta - set(meta))
    if missing_meta:
        raise ValueError(f"experiment_meta missing fields: {missing_meta}")
    for key in ("cpu_cores", "mem_gb", "disk_bw_mb"):
        if key not in machine:
            raise ValueError(f"experiment_meta.machine_spec missing {key}")
    if meta.get("method") != "brute_force_grid_search":
        raise ValueError(f"unexpected method: {meta.get('method')}")

    if len(backends) != 1:
        raise ValueError(f"expected exactly one authoritative validation backend, got {sorted(backends)}")
    backend = next(iter(backends))

    output_dir_raw = str(tuning.get("output_dir") or "").strip()
    if not output_dir_raw:
        raise ValueError("binary_plan_tuning.output_dir is missing")
    output_dir = Path(output_dir_raw).resolve()
    artifact = args.artifact.resolve() if args.artifact else output_dir / "structured_experiment.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)

    summary_rounds, explain_rounds = explain_round_coverage(log_path)
    if len(summary_rounds) >= args.min_rounds and summary_rounds == explain_rounds:
        backfill_output = json.dumps(
            {
                "log": str(log_path),
                "added_rounds": [],
                "skipped": "all_round_summaries_already_have_explain_samples",
                "covered_rounds": explain_rounds,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    else:
        backfill_proc = run(
            [
                sys.executable,
                str(BACKFILL),
                "--config", str(config_path),
                "--log", str(log_path),
                "--max-queries", str(max(1, args.max_explain_queries)),
            ],
            timeout=1800,
        )
        backfill_output = require_ok(backfill_proc, "EXPLAIN backfill")

    export_command = [
        sys.executable,
        str(EXPORTER),
        str(log_path),
        str(artifact),
        "--exp-id", str(meta["exp_id"]),
        "--benchmark", str(meta["benchmark"]),
        "--db", str(meta["db"]),
        "--method", str(meta["method"]),
        "--bottleneck", str(meta["bottleneck"]),
        "--repeat-id", str(meta["repeat_id"]),
        "--cpu-cores", str(machine["cpu_cores"]),
        "--mem-gb", str(machine["mem_gb"]),
        "--disk-bw-mb", str(machine["disk_bw_mb"]),
        "--validation-backend", backend,
        "--expected-candidates-per-round", str(args.expected_candidates_per_round),
    ]
    if meta.get("bottleneck_target") is not None:
        export_command.extend(["--bottleneck-target", str(meta["bottleneck_target"])])
    export_output = require_ok(run(export_command, timeout=600), "structured export")

    verify_command = [
        sys.executable,
        str(args.verifier.resolve()),
        str(artifact),
        "--expected-candidates-per-round", str(args.expected_candidates_per_round),
        "--min-rounds", str(args.min_rounds),
        "--max-bottleneck-drop-pct", str(args.max_bottleneck_drop_pct),
    ]
    if str(meta.get("bottleneck")) == "none":
        verify_command.append("--require-none-all-resources-up")
    verify_proc = run(verify_command, timeout=600)
    status, exit_code, verify_payload = classify_verification(verify_proc)

    physical_exp_id = str(meta["exp_id"])
    logical_alias_results: List[Dict[str, Any]] = []
    logical_exp_ids = list(dict.fromkeys(str(value).strip() for value in args.logical_exp_id))
    logical_exp_ids = [value for value in logical_exp_ids if value and value != physical_exp_id]
    physical_document = read_json(artifact)
    for logical_exp_id in logical_exp_ids:
        alias_artifact = logical_alias_path(artifact, logical_exp_id)
        alias_document = copy.deepcopy(physical_document)
        alias_meta = alias_document.get("meta")
        alias_audit = alias_document.get("audit")
        if not isinstance(alias_meta, dict) or not isinstance(alias_audit, dict):
            raise ValueError("physical artifact lacks meta/audit objects required for logical alias")
        alias_meta["exp_id"] = logical_exp_id
        alias_audit["physical_exp_id"] = physical_exp_id
        alias_audit["physical_artifact"] = str(artifact)
        alias_artifact.write_text(
            json.dumps(alias_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        alias_verify_command = list(verify_command)
        alias_verify_command[2] = str(alias_artifact)
        alias_status, alias_exit_code, alias_verify_payload = classify_verification(
            run(alias_verify_command, timeout=600)
        )
        alias_verification_path = alias_artifact.with_suffix(".verification.json")
        alias_result = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": alias_status,
            "artifact": str(alias_artifact),
            "verification_path": str(alias_verification_path),
            "logical_exp_id": logical_exp_id,
            "physical_exp_id": physical_exp_id,
            "physical_artifact": str(artifact),
            "config": str(config_path),
            "log": str(log_path),
            "validation_backend": backend,
            "verification": alias_verify_payload,
        }
        alias_verification_path.write_text(
            json.dumps(alias_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logical_alias_results.append(alias_result)
        if alias_exit_code == 1:
            exit_code = 1
        elif alias_exit_code == 10 and exit_code == 0:
            exit_code = 10

    result = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "artifact": str(artifact),
        "config": str(config_path),
        "log": str(log_path),
        "validation_backend": backend,
        "logical_aliases": logical_alias_results,
        "backfill": last_json_object(backfill_output),
        "export_output": export_output,
        "verification": verify_payload,
    }
    verification_path = artifact.with_suffix(".verification.json")
    verification_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
