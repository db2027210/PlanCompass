#!/usr/bin/env python3
"""Build a fail-closed XP accepted=4 authority bridge for EXPLAIN generation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping


PARAMETERS = (
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "cpu_tuple_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
    "random_page_cost",
    "seq_page_cost",
)
RESOURCES = ("cpu", "io", "memory")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def read_counter(path: Path, expected: int) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if raw != str(expected):
        raise ValueError(f"{path} expected {expected}, observed {raw!r}")
    return expected


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> Dict[str, Any]:
    config_path = args.config.resolve()
    formal_manifest_path = args.formal_manifest.resolve()
    history_path = args.history.resolve()
    summary_path = args.baseline_summary.resolve()
    selection_path = args.seed_selection.resolve()
    query_snapshot_path = args.query_snapshot.resolve()
    accepted_counter = args.accepted_counter.resolve()
    formal_replay_counter = args.formal_replay_counter.resolve()
    global_counter = args.global_counter.resolve()

    config = read_json(config_path)
    formal_manifest = read_json(formal_manifest_path)
    summary = read_json(summary_path)
    selection = read_json(selection_path)
    query_snapshot = read_json(query_snapshot_path)
    if not all(isinstance(value, Mapping) for value in (config, formal_manifest, summary, selection, query_snapshot)):
        raise ValueError("all JSON authorities must be objects")

    read_counter(accepted_counter, 4)
    read_counter(formal_replay_counter, 22)
    read_counter(global_counter, 22)
    if sha256_file(accepted_counter) != "7de1555df0c2700329e815b93b32c571c3ea54dc967b89e81ab73b9972b72d1d":
        raise ValueError("accepted counter SHA mismatch")
    if formal_manifest.get("accepted_split", {}).get("official_total") != 4:
        raise ValueError("formal manifest does not attest official accepted=4")
    if formal_manifest.get("cpu_affinity") != "32-51,288-307":
        raise ValueError("XP CPU affinity authority mismatch")
    if formal_manifest.get("mysql_exclusion") != "do_not_touch_cpu_184-199,440-455_or_mysql_state":
        raise ValueError("XP MySQL exclusion authority mismatch")
    package_hashes = formal_manifest.get("package_sha256") or {}
    if package_hashes.get("config.json") != sha256_file(config_path):
        raise ValueError("formal manifest does not bind the current config")

    params = config.get("initial_params")
    if not isinstance(params, Mapping) or set(params) != set(PARAMETERS):
        raise ValueError("current config initial_params are incomplete")
    current_params = {name: finite(params[name], f"initial_params.{name}") for name in PARAMETERS}
    if not math.isclose(current_params["seq_page_cost"], 0.2, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("current accepted=4 seq_page_cost is not 0.2")
    effective = summary.get("effective_parameters")
    if not isinstance(effective, Mapping):
        raise ValueError("baseline summary lacks effective_parameters")
    for name, value in current_params.items():
        if not math.isclose(finite(effective[name], f"effective_parameters.{name}"), value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"baseline summary parameter mismatch: {name}")
    if (
        summary.get("status") != "ok"
        or summary.get("technical_failure") is not False
        or summary.get("correctness_passed") is not True
        or summary.get("stable") is not True
    ):
        raise ValueError("baseline summary did not pass correctness and stability")

    aggregate = summary.get("formal_pg_resource", {}).get("aggregate")
    caps = summary.get("formal_pg_resource", {}).get("caps")
    if not isinstance(aggregate, Mapping) or not isinstance(caps, Mapping):
        raise ValueError("baseline summary lacks formal resource aggregate/caps")
    initial_resource = {
        "cpu": finite(aggregate["cpu_utilization"], "cpu_utilization")
        * finite(caps["cpu_quota_cores"], "cpu_quota_cores"),
        "memory": finite(aggregate["memory_working_set_bytes"], "memory_working_set_bytes")
        / (1024.0 ** 3),
        "io": finite(aggregate["total_bps"], "total_bps") / (1024.0 ** 2),
    }

    ratio_events = []
    baseline_events = []
    with history_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid history JSON at line {line_number}") from error
            if not isinstance(event, Mapping):
                continue
            if event.get("event") == "ratio_scale_baseline_measurement_protocol":
                protocol = event.get("measurement_protocol") or {}
                if protocol.get("summary_sha256") == sha256_file(summary_path):
                    ratio_events.append(event)
            elif event.get("event") == "baseline_top_sql":
                profile = event.get("plan_estimator_workload_profile") or {}
                protocol = (profile.get("runtime_resource_stats") or {}).get(
                    "candidate_measurement_protocol"
                ) or {}
                if protocol.get("summary_sha256") == sha256_file(summary_path):
                    baseline_events.append(event)
    if len(ratio_events) != 2:
        raise ValueError(f"expected two sealed ratio baseline attestations, got {len(ratio_events)}")
    if len(baseline_events) != 1:
        raise ValueError(f"expected one baseline resource anchor, got {len(baseline_events)}")
    raw = baseline_events[0]["plan_estimator_workload_profile"]["runtime_resource_raw"]
    expected_resource = {
        "cpu": finite(raw["cpu"], "history cpu"),
        "memory": finite(raw["memory"], "history memory") / (1024.0 ** 3),
        "io": finite(raw["io"], "history io") / (1024.0 ** 2),
    }
    for name in RESOURCES:
        if not math.isclose(initial_resource[name], expected_resource[name], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"history and summary resource mismatch: {name}")

    if (
        query_snapshot.get("contract") != "xp_v5_authority_query_snapshot_v3"
        or query_snapshot.get("query_count") != 31
        or query_snapshot.get("capture_count") != 3
        or query_snapshot.get("consistency_proof", {}).get("passed") is not True
    ):
        raise ValueError("refreshed 3x31 query authority is incomplete")
    candidates = selection.get("top5")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], Mapping):
        raise ValueError("seed selection lacks a first candidate")
    seed = copy.deepcopy(dict(candidates[0]))
    metadata = seed.get("final_estimation_metadata") or {}
    query_features = metadata.get("query_features") if isinstance(metadata, Mapping) else None
    if not isinstance(query_features, list) or len(query_features) != 31:
        raise ValueError("seed selection is not bound to 31 query features")
    seed["initial_params"] = copy.deepcopy(current_params)
    seed["initial_resource"] = copy.deepcopy(initial_resource)
    seed["current_authority_bridge"] = {
        "contract": "xp_current_accepted4_authority_bridge_v1",
        "official_accepted_count": 4,
        "config_sha256": sha256_file(config_path),
        "formal_manifest_sha256": sha256_file(formal_manifest_path),
        "history_sha256": sha256_file(history_path),
        "baseline_summary_sha256": sha256_file(summary_path),
        "query_snapshot_sha256": sha256_file(query_snapshot_path),
    }
    bridge_selection = {
        "selection_contract": "xp_current_accepted4_explain_seed_v1",
        "top5": [seed],
        "source_selection_path": str(selection_path),
        "source_selection_sha256": sha256_file(selection_path),
        "diagnostic_only": True,
        "qps_used_for_inner_scoring": False,
    }

    accepted_incumbent = {
        "contract": "xp_current_accepted4_incumbent_v1",
        "accepted_count": 4,
        "accepted_event": {
            "accepted_count": 4,
            "params": copy.deepcopy(current_params),
            "initial_resource": copy.deepcopy(initial_resource),
            "source": "sealed_cont16b_current_config_and_baseline",
        },
        "params": copy.deepcopy(current_params),
        "formal_update_allowed": False,
        "qps_used_for_inner_scoring": False,
    }
    write_json(args.output_selection.resolve(), bridge_selection)
    write_json(args.output_incumbent.resolve(), accepted_incumbent)
    manifest = {
        "contract": "xp_current_accepted4_authority_bridge_manifest_v1",
        "official_accepted_count": 4,
        "inherited_accepted_count": 3,
        "prior_formal_accepted_count": 1,
        "current_batch_added_accepted_count": 0,
        "initial_params": current_params,
        "initial_resource": initial_resource,
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "184-199,440-455",
        "qps_used_for_inner_scoring": False,
        "sources": {
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "formal_manifest": {
                "path": str(formal_manifest_path),
                "sha256": sha256_file(formal_manifest_path),
            },
            "history": {"path": str(history_path), "sha256": sha256_file(history_path)},
            "baseline_summary": {
                "path": str(summary_path),
                "sha256": sha256_file(summary_path),
            },
            "query_snapshot": {
                "path": str(query_snapshot_path),
                "sha256": sha256_file(query_snapshot_path),
            },
            "source_selection": {
                "path": str(selection_path),
                "sha256": sha256_file(selection_path),
            },
            "accepted_counter": {
                "path": str(accepted_counter),
                "sha256": sha256_file(accepted_counter),
                "value": 4,
            },
            "formal_replay_counter": {
                "path": str(formal_replay_counter),
                "sha256": sha256_file(formal_replay_counter),
                "value": 22,
            },
            "global_counter": {
                "path": str(global_counter),
                "sha256": sha256_file(global_counter),
                "value": 22,
            },
        },
        "outputs": {
            "selection": {
                "path": str(args.output_selection.resolve()),
                "sha256": sha256_file(args.output_selection.resolve()),
            },
            "incumbent": {
                "path": str(args.output_incumbent.resolve()),
                "sha256": sha256_file(args.output_incumbent.resolve()),
            },
        },
    }
    write_json(args.output_manifest.resolve(), manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--formal-manifest", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--baseline-summary", type=Path, required=True)
    parser.add_argument("--seed-selection", type=Path, required=True)
    parser.add_argument("--query-snapshot", type=Path, required=True)
    parser.add_argument("--accepted-counter", type=Path, required=True)
    parser.add_argument("--formal-replay-counter", type=Path, required=True)
    parser.add_argument("--global-counter", type=Path, required=True)
    parser.add_argument("--output-selection", type=Path, required=True)
    parser.add_argument("--output-incumbent", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args), indent=2, sort_keys=True), flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
