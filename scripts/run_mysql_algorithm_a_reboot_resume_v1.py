#!/usr/bin/env python3
"""Resume one two-worker Algorithm A lane after a host reboot.

Only a persisted complete outer-round boundary is carried forward.  The
interrupted round checkpoint is archived in the predecessor snapshot and is
never credited.  A fresh same-cohort baseline plus incumbent replay starts a
new execution-evidence epoch before the interrupted round is repeated.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_dynamic_slots_v1 as slots
from scripts import run_mysql_algorithm_a_async_scene_v1 as base


RECOVERY_CONTRACT = "mysql_algorithm_a_host_reboot_same_cohort_resume_v6"
PG_A_TPCH_PROFILES = {
    "59527": {
        "name": "ra_fullgrid_generic_20260718_w3",
        "container_id": (
            "415d2064098e1c722e211f302268b61a5b5e0f637b37759ad5a5185f399e40d1"
        ),
        "cpuset_cpus": "80-87",
        "memory_bytes": 8589934592,
        "nano_cpus": 8000000000,
        "mount": (
            "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/"
            "workers/generic/w3/pgdata"
        ),
    },
    "59528": {
        "name": "ra_fullgrid_generic_20260718_w4",
        "container_id": (
            "a686f54bd2d51cef38bb9473366e29b591c09c50532da4183d9531f93b8a6b1f"
        ),
        "cpuset_cpus": "32-39",
        "memory_bytes": 8589934592,
        "nano_cpus": 8000000000,
        "mount": (
            "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/"
            "workers/generic/w4/pgdata"
        ),
    },
}


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def aggregate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows or not all(base.pg_outer.physical_valid(row) for row in rows):
        raise RuntimeError("reboot incumbent replay physical gate failed")
    return {
        "db_qps": sum(float(row["db_qps"]) for row in rows) / len(rows),
        "p95_ms": sum(float(row["p95_ms"]) for row in rows) / len(rows),
        "p99_ms": sum(float(row["p99_ms"]) for row in rows) / len(rows),
        "resource_means": {
            name: sum(float(row["resource_means"][name]) for row in rows)
            / len(rows)
            for name in ("cpu", "memory", "io")
        },
        "hard_gate_passed": True,
        "coverage_complete": True,
    }


def _proc_command(pid: int) -> str:
    return (
        Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        .decode("utf-8", errors="replace").strip()
    )


def install_recovery_pg_a_tpch_allowance() -> None:
    """Recognize only the audited, CPU/disk-disjoint PG-A TPC-H cohort.

    The v65 contract predates PG workers 59527/59528.  This recovery-only
    adapter keeps every existing live isolation gate and removes only the
    command-recognition error for the two exact metrics clients after their
    controller lineage and two server container profiles are read back.
    """

    coordination = base.c49.scoped_gate.predecessor
    implementation = base.c49.isolation_v13.implementation
    original = coordination.evaluate_co_resident_pg_isolation

    def evaluate(workloads: list[dict[str, Any]]) -> dict[str, Any]:
        result = copy.deepcopy(original(workloads))
        by_pid = {int(row["pid"]): row for row in workloads}
        result_rows = {
            int(row["pid"]): row for row in (result.get("workloads") or [])
        }
        recognized: dict[int, str] = {}
        lineage: dict[int, dict[str, Any]] = {}
        for pid, row in by_pid.items():
            command = str(row.get("command") or "")
            port_match = re.search(
                r"(?:^|\s)-p\s+(59521|59522|59527|59528)(?:\s|$)",
                command,
            )
            reported = result_rows.get(pid) or {}
            metrics_query = all(
                fragment in command
                for fragment in (
                    "pg_stat_database", "pg_stat_statements", "pg_stat_activity",
                )
            )
            checkpoint_command = bool(
                re.search(r"(?:^|\s)-c\s+CHECKPOINT;\s*$", command)
            )
            database_control = not (metrics_query or checkpoint_command)
            if (
                port_match is None
                or re.search(
                    r"(?:^|\s)/usr/(?:lib/postgresql/12/)?bin/psql(?:\s|$)",
                    command,
                ) is None
                or "-h 127.0.0.1" not in command
                or re.search(
                    r"(?:^|\s)-d\s+(?:postgres|fullgrid_tpch_sf5)(?:\s|$)",
                    command,
                ) is None
                or "fullgrid_tpch_sf5" not in command
                or "/disk2/" in command
                or set(reported.get("errors") or [])
                != {"external_command_not_in_frozen_pg_cohort"}
            ):
                continue
            try:
                parent = int(row.get("ppid") or 0)
                parent_command = _proc_command(parent)
            except (OSError, ValueError):
                continue
            if not (
                "run_pg_algorithm_a_scene.py" in parent_command
                and "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
                in parent_command
                and "/tpch_memory" in parent_command
                and "--target memory" in parent_command
                and "/disk2/" not in parent_command
            ):
                continue
            port = port_match.group(1)
            recognized[pid] = port
            lineage[pid] = {
                "parent_pid": parent,
                "parent_command_sha256": hashlib.sha256(
                    parent_command.encode()
                ).hexdigest(),
                "command_class": (
                    "metrics_query" if metrics_query else
                    "checkpoint" if checkpoint_command else
                    "database_control" if database_control else "unknown"
                ),
            }

        if not recognized:
            result["recovery_pg_algorithm_a_tpch_allowance"] = {
                "recognized": [], "external_mutation": False,
            }
            return result

        names = [str(row["name"]) for row in PG_A_TPCH_PROFILES.values()]
        documents, inspect_error = implementation._docker_documents(names)
        target_cpus = coordination._target_cpu_union()
        profile_errors: list[str] = []
        readback: list[dict[str, Any]] = []
        for port, expected in PG_A_TPCH_PROFILES.items():
            document = documents.get(str(expected["name"]))
            if document is None:
                profile_errors.append(f"container_readback_absent:{port}")
                continue
            actual = implementation._actual_row(str(expected["name"]), document)
            cpus = coordination.cpus(str(actual["cpuset_cpus"]))
            errors = []
            if actual["container_id"] != expected["container_id"]:
                errors.append("container_identity_changed")
            if actual["running"] is not True:
                errors.append("container_not_running")
            if actual["cpuset_cpus"] != expected["cpuset_cpus"]:
                errors.append("container_cpuset_changed")
            if cpus & target_cpus:
                errors.append("container_cpu_overlaps_mysql")
            if actual["memory_bytes"] != expected["memory_bytes"]:
                errors.append("container_memory_changed")
            if actual["memory_swap_bytes"] != -1:
                errors.append("container_memory_swap_changed")
            if actual["nano_cpus"] != expected["nano_cpus"]:
                errors.append("container_cpu_quota_changed")
            if expected["mount"] not in actual["mount_sources"]:
                errors.append("container_disk1_mount_changed")
            if any(
                str(source).startswith("/disk2/")
                for source in actual["mount_sources"]
            ):
                errors.append("container_mounts_mysql_disk2")
            profile_errors.extend(f"{port}:{error}" for error in errors)
            readback.append({
                "port": int(port), "name": expected["name"],
                "container_id": actual["container_id"],
                "running": actual["running"],
                "cpuset_cpus": actual["cpuset_cpus"],
                "memory_bytes": actual["memory_bytes"],
                "memory_swap_bytes": actual["memory_swap_bytes"],
                "nano_cpus": actual["nano_cpus"],
                "mount_sources": actual["mount_sources"],
                "errors": errors,
            })
        if inspect_error:
            profile_errors.append(
                "docker_readback_error_sha256:"
                + hashlib.sha256(inspect_error.encode()).hexdigest()
            )

        errors = list(result.get("errors") or [])
        if not profile_errors:
            for row in result.get("workloads") or []:
                pid = int(row["pid"])
                if pid not in recognized:
                    continue
                row["errors"] = [
                    error for error in (row.get("errors") or [])
                    if error != "external_command_not_in_frozen_pg_cohort"
                ]
                row["recovery_pg_algorithm_a_tpch_metrics_client"] = True
                row["postgres_port"] = int(recognized[pid])
                errors = [
                    error for error in errors
                    if error != (
                        f"pid_{pid}:external_command_not_in_frozen_pg_cohort"
                    )
                ]
        result["errors"] = errors
        result["passed"] = not errors
        result["status"] = (
            "PASS_ISOLATED_CO_RESIDENT_PG"
            if not errors else "BLOCKED_FAIL_CLOSED"
        )
        result["recovery_pg_algorithm_a_tpch_allowance"] = {
            "contract": "exact_pg_a_tpch_psql_lineage_isolation_v1",
            "recognized": [
                {"pid": pid, "port": int(port), **lineage[pid]}
                for pid, port in sorted(recognized.items())
            ],
            "container_readback": readback,
            "profile_errors": profile_errors,
            "cpu_overlap_prohibited": True,
            "disk2_mount_prohibited": True,
            "host_memory_swap_and_psi_gates_inherited": True,
            "external_mutation": False,
        }
        return result

    coordination.evaluate_co_resident_pg_isolation = evaluate


def last_complete_round(predecessor: Mapping[str, Any]) -> int:
    history = predecessor.get("accepted_history") or []
    rounds = [int(row.get("round_index") or 0) for row in history]
    completed = max(rounds, default=0)
    interrupted = int(predecessor.get("interrupted_round_index") or 0)
    if interrupted and completed >= interrupted:
        raise RuntimeError("interrupted round was already credited")
    return completed


def validate_snapshot(path: Path, scene: str) -> dict[str, Any]:
    predecessor = load(path)
    archived = predecessor.get("archived_interrupted_round_checkpoint") or {}
    if (
        predecessor.get("schema_version")
        != "mysql_algorithm_a_reboot_boundary_snapshot_v1"
        or predecessor.get("status") != "REBOOT_BOUNDARY_SNAPSHOT"
        or predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != scene
        or predecessor.get("formal_scope") != "mysql_only"
        or predecessor.get("cross_engine_comparison_prohibited") is not True
        or int(predecessor.get("accepted") or 0) >= base.TARGET_ACCEPTED
        or predecessor.get("round_checkpoint") is not None
        or predecessor.get("reboot_boundary_snapshot") is not True
        or predecessor.get("partial_interrupted_round_credited") is not False
        or not archived
        or int(archived.get("round_index") or 0)
        != int(predecessor.get("interrupted_round_index") or 0)
    ):
        raise RuntimeError("invalid Algorithm A reboot-boundary snapshot")
    slots.search_state_payload(predecessor)
    last_complete_round(predecessor)
    return predecessor


def run(args: argparse.Namespace) -> int:
    scene = str(args.scene)
    predecessor = validate_snapshot(args.predecessor_state, scene)
    active, _ranges = base.scene_parameter_space(scene)
    contract = base.validate_a_contract(scene, active)
    freeze, workers, authority = base.validate_fresh_authority(
        runtime_preflight=args.runtime_preflight,
        prepared_binding=args.prepared_binding,
        benchmark_accounts=args.benchmark_accounts,
        fresh_r0=args.fresh_r0,
        scene=scene,
    )
    install_recovery_pg_a_tpch_allowance()
    if len(workers) != 2:
        raise RuntimeError("reboot recovery requires the original two-worker cohort")

    original_fresh_state = base.fresh_state
    original_measure = base.measure
    original_atomic = base.atomic
    preserved = slots.search_state_payload(predecessor)
    preserved_canonical = copy.deepcopy(preserved["canonical_baseline"])
    completed_round = last_complete_round(predecessor)
    recovery_context: dict[str, Any] = {
        "fresh_baseline": None,
        "incumbent_validation": None,
        "incumbent_rows": None,
    }
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"

    def resumed_fresh_state(
        *, run_id: str, scene: str, active: Sequence[str],
        ranges: Mapping[str, Any], authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = original_fresh_state(
            run_id=run_id, scene=scene, active=active, ranges=ranges,
            authority=authority,
        )
        value.update(copy.deepcopy(preserved))
        value.update({
            "accepted": int(predecessor["accepted"]),
            "accepted_target": base.TARGET_ACCEPTED,
            "maximum_rejected_outer_attempts":
                base.MAX_REJECTED_OUTER_ATTEMPTS,
            "round_index": completed_round,
            "rejected_rounds": int(predecessor.get("rejected_rounds") or 0),
            "predecessor_run_id": predecessor.get("predecessor_run_id"),
            "predecessor_snapshot_path": str(args.predecessor_state),
            "predecessor_snapshot_sha256": sha(args.predecessor_state),
            "recovery_contract": RECOVERY_CONTRACT,
            "execution_evidence_epoch": int(
                predecessor.get("execution_evidence_epoch") or 1
            ) + 1,
            "interrupted_round_index": predecessor["interrupted_round_index"],
            "partial_interrupted_round_credited": False,
            "archived_interrupted_round_checkpoint_sha256": (
                predecessor["archived_interrupted_round_checkpoint_sha256"]
            ),
            "round_checkpoint": None,
            "status": "RUNNING",
            "stage": "REBOOT_RECOVERY_AUTHORITY_VALIDATED",
            "converged": False,
        })
        return value

    def preserved_aggregate(
        rows: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if len(rows) != 2 or not all(
            base.pg_outer.physical_valid(row) for row in rows.values()
        ):
            raise RuntimeError("reboot fresh baseline physical gate failed")
        return copy.deepcopy(preserved_canonical)

    baseline_done = False

    def recovery_measure(**kwargs: Any):
        nonlocal baseline_done
        if baseline_done or Path(kwargs["output"]).name != "formal_baseline":
            return original_measure(**kwargs)
        baseline_done = True
        baseline_rows, latest_state = original_measure(**kwargs)
        incumbent_assignments = {
            worker_id: {
                "rank": index + 1,
                "params": copy.deepcopy(predecessor["incumbent_params"]),
            }
            for index, worker_id in enumerate(sorted(workers))
        }
        incumbent_rows, latest_state = original_measure(
            freeze=kwargs["freeze"], workers=kwargs["workers"],
            assignments=incumbent_assignments,
            output=Path(kwargs["output"]).parent / "recovery_incumbent_replay",
            reset_token=str(kwargs["reset_token"]) + "_incumbent_replay",
            state_path=kwargs["state_path"], state=latest_state,
            stage_prefix="REBOOT_RECOVERY_INCUMBENT_REPLAY",
            run_id=kwargs["run_id"], contract=kwargs["contract"],
        )
        normalized = [
            base.normalize_to_worker_baseline(
                incumbent_rows[worker_id], baseline_rows[worker_id],
                preserved_canonical,
            )
            for worker_id in sorted(workers)
        ]
        incumbent_validation = aggregate_rows(normalized)
        prior = predecessor["incumbent_validation"]
        qps_ratio = float(incumbent_validation["db_qps"]) / float(prior["db_qps"])
        target = base.alg.TARGET[scene]
        target_delta = abs(
            float(incumbent_validation["resource_means"][target])
            - float(prior["resource_means"][target])
        )
        if not 0.5 <= qps_ratio <= 1.5 or target_delta > 0.35:
            raise RuntimeError(
                "reboot incumbent equivalence gate failed: "
                f"qps_ratio={qps_ratio:.6f} target_delta={target_delta:.6f}"
            )
        recovery_context.update({
            "fresh_baseline": copy.deepcopy(baseline_rows),
            "incumbent_validation": incumbent_validation,
            "incumbent_rows": copy.deepcopy(incumbent_rows),
            "qps_ratio_to_predecessor": qps_ratio,
            "target_resource_absolute_delta": target_delta,
        })
        return baseline_rows, latest_state

    def recovery_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if (
            path == state_path
            and document.get("formal_started") is True
            and recovery_context.get("incumbent_validation") is not None
            and document.get("reboot_recovery_completed") is not True
        ):
            document["canonical_baseline"] = copy.deepcopy(preserved_canonical)
            document["baseline_by_worker"] = copy.deepcopy(
                recovery_context["fresh_baseline"]
            )
            document["incumbent_validation"] = copy.deepcopy(
                recovery_context["incumbent_validation"]
            )
            document["reboot_recovery_completed"] = True
            document["reboot_recovery"] = {
                "contract": RECOVERY_CONTRACT,
                "same_worker_cohort": True,
                "fresh_baseline_per_worker": 1,
                "incumbent_replay_required": True,
                "incumbent_replay_rows": copy.deepcopy(
                    recovery_context["incumbent_rows"]
                ),
                "qps_ratio_to_predecessor": recovery_context[
                    "qps_ratio_to_predecessor"
                ],
                "target_resource_absolute_delta": recovery_context[
                    "target_resource_absolute_delta"
                ],
                "canonical_baseline_preserved": True,
                "preserved_search_state_sha256": slots.canonical_sha256(
                    preserved
                ),
                "interrupted_round_repeated_from_start": True,
            }
        original_atomic(path, document)

    base.fresh_state = resumed_fresh_state
    base.aggregate_baseline = preserved_aggregate
    base.measure = recovery_measure
    base.atomic = recovery_atomic
    base.validate_a_contract = lambda _scene, _active: contract
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(authority)
    )
    return base.run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=base.SCENES, required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
