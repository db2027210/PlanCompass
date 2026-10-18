#!/usr/bin/env python3
"""Read-only audit of the JOB v49 discovery-to-exact-repeat boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping


DEFAULT_ATTEMPT_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
FORMAL_CAMPAIGN = "job_final_algorithm_fresh10_v36_20260731T2020"
AUTONOMOUS_ROOT = "autonomous_target10_v49"
NEUTRAL_FLAGS = {
    "accepted": False,
    "gradient": False,
    "incumbent": False,
    "range": False,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def read_counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise ValueError(f"invalid counter: {path}")
    return int(raw)


def canonical_params(selection: Mapping[str, Any]) -> set[str]:
    top5 = selection.get("top5")
    if not isinstance(top5, list):
        return set()
    result = set()
    for item in top5:
        params = item.get("candidate_params") if isinstance(item, Mapping) else None
        if isinstance(params, Mapping):
            result.add(json.dumps(params, sort_keys=True, separators=(",", ":")))
    return result


class Audit:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def check(self, name: str, passed: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            self.errors.append(name if detail is None else f"{name}: {detail}")


def validate_summary(
    audit: Audit,
    summary: Mapping[str, Any],
    *,
    selection_sha: str,
    label: str,
) -> Dict[str, Any]:
    physical = int(summary.get("physical_invocation_count", -1))
    real_physical = int(summary.get("real_workload_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    attempts = valid + technical
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    invocations = summary.get("invocations") or []
    ledgers_are_lists = all(
        isinstance(items, list) for items in (successful, failures, invocations)
    )
    audit.check(f"{label}.ledgers_are_lists", ledgers_are_lists)
    if not ledgers_are_lists:
        return {
            "physical": physical,
            "valid": valid,
            "technical": technical,
            "attempts": attempts,
            "clean5": False,
        }
    all_records_are_objects = all(
        isinstance(item, Mapping)
        for items in (successful, failures, invocations)
        for item in items
    )
    audit.check(f"{label}.ledger_records_are_objects", all_records_are_objects)
    if not all_records_are_objects:
        return {
            "physical": physical,
            "valid": valid,
            "technical": technical,
            "attempts": attempts,
            "clean5": False,
        }
    successful_ranks = [int(item.get("rank", -1)) for item in successful]
    failure_ranks = [int(item.get("rank", -1)) for item in failures]
    invocation_ranks = [int(item.get("rank", -1)) for item in invocations]
    attempt_ranks = successful_ranks + failure_ranks
    missing_ranks = sorted(set(failure_ranks) - set(invocation_ranks))
    audit.check(f"{label}.diagnostic_only", summary.get("diagnostic_only") is True)
    audit.check(
        f"{label}.selection_sha",
        summary.get("selection_sha256") == selection_sha,
    )
    audit.check(
        f"{label}.physical_cap",
        0 <= physical <= 5 and real_physical == physical,
        {"physical": physical, "real": real_physical},
    )
    audit.check(
        f"{label}.attempt_budget",
        attempts == 5 and 0 <= valid <= physical <= attempts,
        {"attempts": attempts, "valid": valid, "technical": technical},
    )
    audit.check(
        f"{label}.ledger_counts",
        len(successful) == valid
        and len(failures) == technical
        and len(invocations) == physical,
    )
    audit.check(
        f"{label}.attempt_ranks",
        len(set(attempt_ranks)) == 5 and sorted(attempt_ranks) == [1, 2, 3, 4, 5],
        attempt_ranks,
    )
    audit.check(
        f"{label}.invocation_ranks",
        len(set(invocation_ranks)) == len(invocation_ranks)
        and set(successful_ranks).issubset(set(invocation_ranks))
        and set(invocation_ranks).issubset(set(attempt_ranks))
        and len(missing_ranks) == attempts - physical,
        {"invocations": invocation_ranks, "pre_apply_failures": missing_ranks},
    )
    audit.check(
        f"{label}.technical_state_neutral",
        all(
            item.get("technical_failure") is True
            and item.get("accepted") is False
            and item.get("gradient_update_applied") is False
            and item.get("incumbent_update_applied") is False
            and item.get("range_update_applied") is False
            for item in failures
        ),
    )
    audit.check(
        f"{label}.successful_state_neutral",
        all(
            item.get("technical_failure") is False
            and item.get("accepted") is False
            and item.get("gradient_update_applied") is False
            and item.get("incumbent_update_applied") is False
            and item.get("range_update_applied") is False
            for item in successful
        ),
    )
    audit.check(
        f"{label}.baseline_restored",
        summary.get("baseline_params_restored") is True
        and (summary.get("baseline_restore") or {}).get("success") is True,
    )
    audit.check(
        f"{label}.state_neutral",
        summary.get("initial_state") == summary.get("final_state")
        and summary.get("formal_update_flags") == NEUTRAL_FLAGS,
    )
    clean5 = physical == 5 and valid == 5 and technical == 0
    return {
        "physical": physical,
        "valid": valid,
        "technical": technical,
        "attempts": attempts,
        "pre_invocation_technical_failure_ranks": missing_ranks,
        "clean5": clean5,
    }


def validate_audit(
    audit: Audit,
    value: Mapping[str, Any],
    *,
    summary_path: Path,
    selection_path: Path,
    metrics: Mapping[str, Any],
    state_sha: str,
    accepted: int,
    label: str,
) -> bool:
    expected_status = (
        "clean5_state_neutral" if metrics.get("clean5") else "technical_state_neutral"
    )
    checks = {
        "status": value.get("status") == expected_status,
        "selection_sha": value.get("selection_sha256") == sha256_file(selection_path),
        "summary_sha": value.get("summary_sha256") == sha256_file(summary_path),
        "physical": int(value.get("physical_calls", -1)) == metrics.get("physical"),
        "attempts": int(value.get("batch_budget_attempts", -1)) == metrics.get("attempts"),
        "valid": int(value.get("valid_replays", -1)) == metrics.get("valid"),
        "technical": int(value.get("technical_failures", -1)) == metrics.get("technical"),
        "accepted_added_zero": int(value.get("accepted_added", -1)) == 0,
        "accepted_pin": int(value.get("accepted_counter", -1)) == accepted,
        "state_sha": value.get("formal_state_sha256") == state_sha,
    }
    for name, passed in checks.items():
        audit.check(f"{label}.audit_{name}", passed)
    return all(checks.values())


def run(args: argparse.Namespace) -> Dict[str, Any]:
    audit = Audit()
    b = args.attempt_root.resolve()
    t = b / FORMAL_CAMPAIGN
    root = t / AUTONOMOUS_ROOT
    cycle_root = root / f"cycle_{args.cycle:03d}"
    controller_path = root / "controller_state.json"
    state_path = t / "package/formal_state.json"
    discovery_path = cycle_root / "discovery_top5.json"
    required_paths = (controller_path, state_path, discovery_path)
    for path in required_paths:
        audit.check(f"required:{path.name}", path.is_file(), str(path))
    if not all(path.is_file() for path in required_paths):
        return {
            "schema_version": "job_v49_discovery_repeat_read_only_audit_v1",
            "status": "fail_closed",
            "errors": audit.errors,
            "warnings": audit.warnings,
            "checks": audit.checks,
            "remote_actions_executed": False,
        }

    controller = load_json(controller_path)
    state = load_json(state_path)
    discovery = load_json(discovery_path)
    state_sha = sha256_file(state_path)
    accepted = int(state.get("accepted_count", -1))
    state_global = int(state.get("global_ordinal", -1))
    state_formal = int(state.get("formal_replay_count", -1))
    external = {
        "global": read_counter(b / "replay_counter.txt"),
        "formal": read_counter(b / "formal_search_replay_counter.txt"),
        "accepted": read_counter(b / "formal_search_accepted_counter.txt"),
    }
    audit.check(
        "controller_schema",
        controller.get("schema_version") == "job_fresh10_target10_autonomous_v49",
    )
    audit.check(
        "controller_state_pin",
        controller.get("formal_state_sha256") == state_sha,
    )
    audit.check("accepted_counter_pin", external["accepted"] == accepted)
    audit.check("formal_counter_pin", external["formal"] == state_formal)

    discovery_replays = sorted(cycle_root.glob("discovery_replay_[0-9][0-9]"))
    audit.check("discovery_batch_cap", len(discovery_replays) <= 5, len(discovery_replays))
    running_global = state_global
    clean_roots: List[str] = []
    batch_results: List[Dict[str, Any]] = []
    incomplete_roots: List[str] = []
    for index, replay_root in enumerate(discovery_replays, 1):
        label = replay_root.name
        selection_path = replay_root / "top5.json"
        before_path = replay_root / "counters_before.json"
        summary_path = replay_root / "replay/summary.json"
        audit_path = replay_root / "diagnostic_audit.json"
        audit.check(f"{label}.selection_exists", selection_path.is_file())
        audit.check(f"{label}.counters_before_exists", before_path.is_file())
        if selection_path.is_file():
            audit.check(
                f"{label}.selection_matches_discovery",
                selection_path.read_bytes() == discovery_path.read_bytes(),
            )
        if before_path.is_file():
            before = load_json(before_path)
            audit.check(
                f"{label}.counter_chain",
                int(before.get("global", -1)) == running_global
                and int(before.get("formal_replay", -1)) == state_formal
                and int(before.get("accepted", -1)) == accepted,
                {"expected_global": running_global, "observed": before},
            )
        if not summary_path.is_file():
            incomplete_roots.append(str(replay_root))
            if index != len(discovery_replays):
                audit.check(f"{label}.incomplete_is_latest", False)
            continue
        metrics = validate_summary(
            audit,
            load_json(summary_path),
            selection_sha=sha256_file(selection_path),
            label=label,
        )
        running_global += int(metrics["physical"])
        audit_valid = False
        if audit_path.is_file():
            audit_valid = validate_audit(
                audit,
                load_json(audit_path),
                summary_path=summary_path,
                selection_path=selection_path,
                metrics=metrics,
                state_sha=state_sha,
                accepted=accepted,
                label=label,
            )
        else:
            audit.warnings.append(f"{label} summary sealed; diagnostic audit pending")
        if metrics["clean5"] and audit_valid:
            clean_roots.append(str(replay_root))
        batch_results.append({"root": str(replay_root), **metrics, "audit_valid": audit_valid})

    if incomplete_roots:
        audit.check(
            "external_global_in_inflight_range",
            running_global <= external["global"] <= running_global + 5,
            {"sealed": running_global, "external": external["global"]},
        )
    else:
        audit.check(
            "external_global_matches_sealed_batches",
            external["global"] == running_global,
            {"expected": running_global, "external": external["global"]},
        )

    exact_repeat_eligible = bool(clean_roots)
    repeat_roots = sorted(cycle_root.glob("repeat_[0-9][0-9]"))
    if repeat_roots:
        audit.check("repeat_has_clean_discovery", exact_repeat_eligible, clean_roots)
        first_repeat = repeat_roots[0]
        first_selection = first_repeat / "top5.json"
        audit.check("repeat_top5_exists", first_selection.is_file())
        if first_selection.is_file():
            repeat_selection = load_json(first_selection)
            top5 = repeat_selection.get("top5")
            roles = [
                item.get("selection_role") if isinstance(item, Mapping) else None
                for item in top5 or []
            ]
            audit.check(
                "repeat_trusted3_probe2",
                roles == ["trusted", "trusted", "trusted", "probe", "probe"],
                roles,
            )
            audit.check(
                "repeat_exact_parameter_set",
                len(canonical_params(discovery)) == 5
                and canonical_params(repeat_selection) == canonical_params(discovery),
            )
            second = first_repeat / "top5.second.json"
            if second.is_file():
                audit.check(
                    "repeat_builder_deterministic",
                    first_selection.read_bytes() == second.read_bytes(),
                )
            else:
                audit.warnings.append("repeat_01 second deterministic build pending")
    elif exact_repeat_eligible:
        audit.warnings.append("clean5 discovery sealed; repeat_01 creation pending")

    phase = controller.get("phase")
    if phase in {"repeat_gate", "formal"}:
        audit.check("controller_repeat_phase_has_clean_discovery", exact_repeat_eligible)
    return {
        "schema_version": "job_v49_discovery_repeat_read_only_audit_v1",
        "status": "fail_closed" if audit.errors else "pass_or_in_progress",
        "controller_phase": phase,
        "formal_state_sha256": state_sha,
        "authoritative_state": {
            "accepted_count": accepted,
            "global_ordinal": state_global,
            "formal_replay_count": state_formal,
        },
        "external_counters": external,
        "discovery_batches": batch_results,
        "incomplete_discovery_roots": incomplete_roots,
        "clean_discovery_roots": clean_roots,
        "exact_repeat_eligible": exact_repeat_eligible,
        "repeat_roots": [str(path) for path in repeat_roots],
        "checks": audit.checks,
        "warnings": audit.warnings,
        "errors": audit.errors,
        "remote_actions_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-root", type=Path, default=DEFAULT_ATTEMPT_ROOT)
    parser.add_argument("--cycle", type=int, default=5)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 2 if result["status"] == "fail_closed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
