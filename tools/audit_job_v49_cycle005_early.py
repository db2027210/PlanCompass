#!/usr/bin/env python3
"""Module."""

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
REQUIRED_CANDIDATE_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)
EARLY_PHASES = {
    "successor_discovery",
    "diagnostic_discovery",
    "repeat_gate",
    "formal",
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
        raise ValueError(f"counter is not a non-negative integer: {path}")
    return int(raw)


class Audit:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def check(self, name: str, passed: bool, detail: Any = None) -> None:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            self.errors.append(name if detail is None else f"{name}: {detail}")


def validate_controller(
    audit: Audit,
    controller: Mapping[str, Any],
    *,
    cycle: int,
) -> None:
    audit.check(
        "controller_schema",
        controller.get("schema_version") == "job_fresh10_target10_autonomous_v49",
        controller.get("schema_version"),
    )
    audit.check("target_accepted_10", controller.get("target_accepted") == 10)
    audit.check(
        "manual_candidates_disabled",
        controller.get("manual_candidate_injection") is False,
    )
    audit.check(
        "qps_outer_only",
        controller.get("qps_used_for_inner_scoring") is False,
    )
    status = controller.get("status")
    audit.check(
        "controller_status_known",
        status in {"running", "complete", "fail_closed", "fail_closed_cycle_cap"},
        status,
    )
    if status == "running":
        audit.check("controller_cycle", int(controller.get("cycle", -1)) == cycle)
        audit.check(
            "controller_phase_known",
            controller.get("phase") in EARLY_PHASES,
            controller.get("phase"),
        )


def validate_inventory(audit: Audit, path: Path) -> set[str]:
    inventory = load_json(path)
    rows = inventory.get("fingerprints")
    sources = inventory.get("sources")
    audit.check(
        "inventory_schema",
        inventory.get("schema_version") == "job_global_three_level_exclusion_v1",
        inventory.get("schema_version"),
    )
    audit.check("inventory_rows_list", isinstance(rows, list))
    audit.check("inventory_sources_list", isinstance(sources, list))
    if not isinstance(rows, list) or not isinstance(sources, list):
        return set()
    audit.check("inventory_sources_nonempty", bool(sources))
    identifiers = [
        str((row.get("three_level_fingerprint") or {}).get("id") or "")
        for row in rows
        if isinstance(row, Mapping)
    ]
    identifier_set = set(identifiers)
    audit.check("inventory_ids_nonempty", "" not in identifier_set)
    audit.check("inventory_ids_unique", len(identifier_set) == len(identifiers))
    audit.check("inventory_ids_sorted", identifiers == sorted(identifiers))
    audit.check(
        "inventory_count",
        int(inventory.get("fingerprint_count", -1)) == len(identifier_set),
        {"declared": inventory.get("fingerprint_count"), "observed": len(identifier_set)},
    )
    seen_sources: set[str] = set()
    source_failures: List[str] = []
    for raw in sources:
        if not isinstance(raw, Mapping):
            source_failures.append("non-object source")
            continue
        source_path = Path(str(raw.get("path") or ""))
        expected = str(raw.get("sha256") or "")
        key = str(source_path)
        if (
            not key
            or key in seen_sources
            or not source_path.is_file()
            or len(expected) != 64
            or sha256_file(source_path) != expected
        ):
            source_failures.append(key or "empty source path")
        seen_sources.add(key)
    audit.check(
        "inventory_source_shas",
        not source_failures,
        source_failures[:10],
    )
    return identifier_set


def validate_discovery(
    audit: Audit,
    discovery: Path,
    second: Path,
    excluded: set[str],
) -> tuple[set[str], Dict[str, Any]]:
    audit.check("discovery_double_generation_complete", discovery.is_file() and second.is_file())
    if not discovery.is_file() or not second.is_file():
        return set(), {}
    audit.check("discovery_byte_deterministic", discovery.read_bytes() == second.read_bytes())
    selection = load_json(discovery)
    top5 = selection.get("top5")
    audit.check("discovery_top5_list", isinstance(top5, list) and len(top5) == 5)
    if not isinstance(top5, list) or len(top5) != 5:
        return set(), selection
    audit.check("discovery_diagnostic_only", selection.get("diagnostic_only") is True)
    roles = [item.get("selection_role") if isinstance(item, Mapping) else None for item in top5]
    audit.check("discovery_probe5", roles == ["probe"] * 5, roles)
    fingerprints: List[str] = []
    candidate_failures: List[int] = []
    for rank, item in enumerate(top5, 1):
        if not isinstance(item, Mapping):
            candidate_failures.append(rank)
            continue
        fingerprint = item.get("three_level_fingerprint") or {}
        identifier = str(fingerprint.get("id") or "") if isinstance(fingerprint, Mapping) else ""
        fingerprints.append(identifier)
        if (
            not identifier
            or item.get("boundary_corner") is not False
            or not isinstance(fingerprint, Mapping)
            or fingerprint.get("valid") is not True
            or any(field not in item for field in REQUIRED_CANDIDATE_FIELDS)
        ):
            candidate_failures.append(rank)
    fingerprint_set = set(fingerprints)
    audit.check("discovery_candidate_contract", not candidate_failures, candidate_failures)
    audit.check(
        "discovery_fingerprints_unique",
        len(fingerprints) == 5 and len(fingerprint_set) == 5 and "" not in fingerprint_set,
    )
    collisions = sorted(fingerprint_set & excluded)
    audit.check("discovery_globally_novel", not collisions, collisions)
    return fingerprint_set, selection


def detected_stage(cycle_root: Path) -> str:
    if (cycle_root / "cycle_audit.json").is_file():
        return "cycle_audited"
    if (cycle_root / "formal/replay/formal_state_commit_transaction.json").is_file():
        return "formal_transaction"
    if (cycle_root / "formal/replay").exists():
        return "formal_replay"
    if (cycle_root / "formal/package").exists():
        return "formal_packaging"
    if list(cycle_root.glob("repeat_[0-9][0-9]")):
        return "repeat_gate"
    if list(cycle_root.glob("discovery_replay_[0-9][0-9]")):
        return "diagnostic_discovery"
    if (cycle_root / "discovery_audit.json").is_file():
        return "discovery_ready"
    if (cycle_root / "discovery_top5.second.json").is_file():
        return "discovery_double_generated"
    if (cycle_root / "discovery_top5.json").is_file():
        return "generator_b_pending"
    if (cycle_root / "global_exclusion.json").is_file():
        return "generator_a_pending"
    return "local_inventory_or_incumbent_scan"


def run(args: argparse.Namespace) -> Dict[str, Any]:
    audit = Audit()
    b = args.attempt_root.resolve()
    t = b / FORMAL_CAMPAIGN
    root = t / AUTONOMOUS_ROOT
    cycle_root = root / f"cycle_{args.cycle:03d}"
    controller_path = root / "controller_state.json"
    state_path = t / "package/formal_state.json"
    for label, path in (("controller_state", controller_path), ("formal_state", state_path)):
        audit.check(f"{label}_exists", path.is_file(), str(path))
    controller = load_json(controller_path) if controller_path.is_file() else {}
    state = load_json(state_path) if state_path.is_file() else {}
    if controller:
        validate_controller(audit, controller, cycle=args.cycle)
    state_sha = sha256_file(state_path) if state_path.is_file() else None
    if controller.get("status") == "running":
        audit.check(
            "controller_state_sha_pin",
            controller.get("formal_state_sha256") == state_sha,
            {"controller": controller.get("formal_state_sha256"), "observed": state_sha},
        )
        audit.check(
            "controller_accepted_pin",
            int(controller.get("accepted_count", -1)) == int(state.get("accepted_count", -2)),
        )
    counters: Dict[str, int] = {}
    for name, path in (
        ("global", b / "replay_counter.txt"),
        ("formal", b / "formal_search_replay_counter.txt"),
        ("accepted", b / "formal_search_accepted_counter.txt"),
    ):
        try:
            counters[name] = read_counter(path)
        except (FileNotFoundError, ValueError) as error:
            audit.errors.append(str(error))
    if counters and state:
        audit.check(
            "accepted_counter_matches_state",
            counters.get("accepted") == int(state.get("accepted_count", -1)),
        )
        audit.check(
            "formal_counter_matches_state",
            counters.get("formal") == int(state.get("formal_replay_count", -1)),
        )
        if controller.get("phase") == "successor_discovery":
            audit.check(
                "early_global_counter_matches_state",
                counters.get("global") == int(state.get("global_ordinal", -1)),
            )

    inventory_path = cycle_root / "global_exclusion.json"
    excluded: set[str] = set()
    if inventory_path.is_file():
        excluded = validate_inventory(audit, inventory_path)
    else:
        audit.warnings.append("global_exclusion.json not sealed yet")

    discovery = cycle_root / "discovery_top5.json"
    second = cycle_root / "discovery_top5.second.json"
    discovery_ids: set[str] = set()
    if discovery.is_file() or second.is_file():
        audit.check("generator_a_log_exists", (cycle_root / "generator_a.log").is_file())
        if second.is_file():
            audit.check("generator_b_log_exists", (cycle_root / "generator_b.log").is_file())
        discovery_ids, _selection = validate_discovery(
            audit,
            discovery,
            second,
            excluded,
        )
    else:
        audit.warnings.append("discovery generation has not sealed an output")

    discovery_audit_path = cycle_root / "discovery_audit.json"
    if discovery_audit_path.is_file():
        discovery_audit = load_json(discovery_audit_path)
        audit.check(
            "discovery_audit_status",
            discovery_audit.get("status")
            == "five_globally_novel_noncorner_probe_fingerprints",
        )
        audit.check(
            "discovery_audit_selection_sha",
            discovery.is_file()
            and discovery_audit.get("selection_sha256") == sha256_file(discovery),
        )
        audit.check(
            "discovery_audit_fingerprints",
            set(discovery_audit.get("fingerprints") or []) == discovery_ids,
        )
        audit.check("discovery_audit_zero_workload", discovery_audit.get("new_workload_calls") == 0)
        audit.check(
            "discovery_audit_state_pin",
            discovery_audit.get("formal_state_sha256") == state_sha,
        )

    return {
        "schema_version": "job_v49_cycle005_early_read_only_audit_v1",
        "status": "fail_closed" if audit.errors else "pass_or_in_progress",
        "detected_stage": detected_stage(cycle_root),
        "controller_state": controller,
        "authoritative_state": {
            "sha256": state_sha,
            "accepted_count": state.get("accepted_count"),
            "global_ordinal": state.get("global_ordinal"),
            "formal_replay_count": state.get("formal_replay_count"),
        },
        "external_counters": counters,
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
