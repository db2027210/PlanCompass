#!/usr/bin/env python3
"""Audit a fixed MySQL formal batch against its explicit acceptance protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def get_result(attempt_dir: Path) -> tuple[Path, dict[str, Any]] | None:
    candidates = sorted(attempt_dir.glob("*_r*.json"))
    if len(candidates) != 1:
        return None
    value = json.loads(candidates[0].read_text(encoding="utf-8"))
    return candidates[0], value if isinstance(value, dict) else {}


def audit_attempt(result: dict[str, Any], *, min_delta: float, max_delta: float) -> dict[str, Any]:
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    acceptance = audit.get("acceptance_metrics") if isinstance(audit.get("acceptance_metrics"), dict) else {}
    output = result.get("result") if isinstance(result.get("result"), dict) else {}
    final_source = audit.get("final_latency_source")
    outer = result.get("outer_loop") if isinstance(result.get("outer_loop"), list) else []
    has_p95_p99 = bool(outer) and all(
        isinstance(row, dict) and "latency_p95_ms" in row and "latency_p99_ms" in row
        for row in (outer[0], outer[-1])
    )
    checks = {
        "explicit_score_cap": audit.get("bottleneck_score_cap_auto_from_baseline") is False,
        "delta_u_in_requested_pp_band": (
            isinstance(acceptance.get("delta_bottleneck_util_pp"), (int, float))
            and min_delta <= float(acceptance["delta_bottleneck_util_pp"]) <= max_delta
        ),
        "qps_gain_at_least_15pct": (
            isinstance(acceptance.get("qps_gain_ratio"), (int, float))
            and float(acceptance["qps_gain_ratio"]) >= 0.15
        ),
        "cpu_policy_met": acceptance.get("non_target_cpu_policy_met") is True,
        "runner_final_acceptance": acceptance.get("final_acceptance_met") is True,
        "not_bad_config": output.get("bad_config") is False,
        "native_p99_source_not_timeout": isinstance(final_source, str) and final_source.startswith("native_hybench_"),
        "p95_and_p99_present": has_p95_p99,
        "independent_final_physical_replay": audit.get("independent_final_real_replay") is True,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "qps_before": output.get("qps_before"),
        "qps_after": output.get("qps_after"),
        "delta_bottleneck_util_pp": acceptance.get("delta_bottleneck_util_pp"),
        "cpu_util_change_pp": acceptance.get("non_target_cpu_util_change_pp"),
        "p95_before_ms": outer[0].get("latency_p95_ms") if outer else None,
        "p95_after_ms": outer[-1].get("latency_p95_ms") if outer else None,
        "p99_before_ms": output.get("p99_before_ms"),
        "p99_after_ms": output.get("p99_after_ms"),
        "wall_time_s": output.get("total_wall_time_s"),
        "final_latency_source": final_source,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    batch = args.batch_dir.resolve()
    manifest_path = batch / "batch_summary.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"batch_summary.json is missing from {batch}")
    summary = json.loads(manifest_path.read_text(encoding="utf-8"))
    attempts: list[dict[str, Any]] = []
    for planned in summary.get("attempts", []):
        index = int(planned["attempt_index"])
        found = get_result(batch / f"attempt_{index:02d}")
        if found is None:
            attempts.append({
                "attempt_index": index,
                "formal_json_present": False,
                "process_exit_code": planned.get("exit_code"),
                "passed": False,
            })
            continue
        path, result = found
        item = audit_attempt(result, min_delta=args.min_delta_pp, max_delta=args.max_delta_pp)
        attempts.append({"attempt_index": index, "formal_json_present": True, "result_path": str(path), **item})
    passed = sum(item.get("passed") is True for item in attempts)
    output = {
        "protocol_id": summary.get("protocol_id"),
        "attempt_limit": summary.get("attempt_limit"),
        "delta_band_pp": [args.min_delta_pp, args.max_delta_pp],
        "attempts": attempts,
        "passed_attempts": passed,
        "required_passes": args.required_passes,
        "acceptance_met": passed >= args.required_passes,
    }
    if args.write:
        write_json(batch / "acceptance_audit.json", output)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--min-delta-pp", type=float, required=True)
    parser.add_argument("--max-delta-pp", type=float, required=True)
    parser.add_argument("--required-passes", type=int, required=True)
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2), flush=True)
