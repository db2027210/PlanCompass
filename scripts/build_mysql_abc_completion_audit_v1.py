#!/usr/bin/env python3
"""Build a fail-closed completion audit for the four-scene MySQL A/B/C runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT = HISTORY / "v1061_mysql_four_scene_abc_completion_audit_20260829/completion_audit.json"
SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def common_checks(document: dict, label: str) -> None:
    require(document.get("formal_scope") == "mysql_only", f"{label}: scope is not mysql_only")
    require(document.get("cross_engine_comparison_prohibited") is True, f"{label}: cross-engine prohibition missing")


def main() -> None:
    rows: list[dict] = []
    reuse_path = HISTORY / "v1028_existing_b_a_reuse_no_rerun_a_check_only_20260828/reuse_manifest.json"
    reuse = load(reuse_path)
    require(reuse.get("status") == "PASS", "reuse manifest is not PASS")
    common_checks(reuse, "reuse manifest")

    for scene in SCENES:
        item = reuse["algorithm_b"]["scenes"][scene]
        path = Path(item["path"])
        state = load(path)
        require(sha256(path) == item["sha256"], f"B/{scene}: state hash changed")
        common_checks(state, f"B/{scene}")
        require(state.get("status") == "COMPLETE", f"B/{scene}: not COMPLETE")
        require(state.get("formal_evidence_eligible") is True, f"B/{scene}: not formally eligible")
        rows.append({
            "algorithm": "B",
            "scene": scene,
            "status": "COMPLETE",
            "accepted_rounds": state["accepted_rounds"][scene],
            "outer_attempt": state["outer_attempt"],
            "physical_replay_parallelism": state["physical_replay_parallelism"],
            "evidence_path": str(path),
            "evidence_sha256": item["sha256"],
        })

    for scene in SCENES[:3]:
        item = reuse["algorithm_a"]["scenes"][scene]
        path = Path(item["path"])
        state = load(path)
        require(sha256(path) == item["sha256"], f"A/{scene}: state hash changed")
        common_checks(state, f"A/{scene}")
        require(state.get("status") == "COMPLETE" and state.get("converged") is True, f"A/{scene}: not converged")
        require(float(state["normalized_width"]) <= 0.01, f"A/{scene}: width above 1%")
        rows.append({
            "algorithm": "A",
            "scene": scene,
            "status": "COMPLETE",
            "accepted_rounds": state["accepted"],
            "outer_attempt": state["round_index"],
            "normalized_width": state["normalized_width"],
            "active_parameters": state["active_parameters"],
            "evidence_path": str(path),
            "evidence_sha256": item["sha256"],
        })

    a_io_path = HISTORY / "v1060_to_a_io_round6_v2_resume_20260828_xp_io/algorithm_a_state.json"
    a_io = load(a_io_path)
    common_checks(a_io, "A/hybench_xp_io")
    require(a_io.get("status") == "COMPLETE" and a_io.get("converged") is True, "A/hybench_xp_io: not converged")
    require(float(a_io["normalized_width"]) <= 0.01, "A/hybench_xp_io: width above 1%")
    require(a_io.get("accepted") == a_io.get("accepted_target") == 7, "A/hybench_xp_io: acceptance target not reached")
    require(a_io["round6_checkpoint_resume"]["candidate_document_equivalence_checked"] is True, "A/hybench_xp_io: checkpoint equivalence missing")
    rows.append({
        "algorithm": "A",
        "scene": "hybench_xp_io",
        "status": "COMPLETE",
        "accepted_rounds": a_io["accepted"],
        "rejected_rounds": a_io["rejected_rounds"],
        "outer_attempt": a_io["round_index"],
        "normalized_width": a_io["normalized_width"],
        "active_parameters": a_io["active_parameters"],
        "evidence_path": str(a_io_path),
        "evidence_sha256": sha256(a_io_path),
    })

    scoped_c = {
        "job_memory": HISTORY / "v965_to_c_job_pg_coverage_mixed_resume_20260828_job/scoped_terminal_status_repair.json",
        "tpch_sf4_memory": HISTORY / "v966_to_c_tpch_pg_coverage_mixed_resume_20260828_tpch/scoped_terminal_status_repair.json",
    }
    for scene, path in scoped_c.items():
        state = load(path)
        common_checks(state, f"C/{scene}")
        require(state.get("status") == "PASS" and state.get("active_scene_converged") is True, f"C/{scene}: repair not PASS")
        require(float(state["interval_normalized_width"]) <= 0.01, f"C/{scene}: width above 1%")
        require(state.get("workload_replayed") is False and state.get("candidate_measurements_mutated") is False, f"C/{scene}: repair changed measurements")
        rows.append({
            "algorithm": "C",
            "scene": scene,
            "status": "COMPLETE",
            "outer_attempt": state["outer_attempt"],
            "normalized_width": state["interval_normalized_width"],
            "terminal_repair_without_replay": True,
            "evidence_path": str(path),
            "evidence_sha256": sha256(path),
        })

    reconciled_c = {
        "hybench_xp_cpu": HISTORY / "v1039_c_cpu_async_terminal_reconciliation_20260828/terminal_reconciliation.json",
        "hybench_xp_io": HISTORY / "v1041_c_io_async_terminal_reconciliation_20260828/terminal_reconciliation.json",
    }
    for scene, path in reconciled_c.items():
        state = load(path)
        common_checks(state, f"C/{scene}")
        require(state.get("status") == "PASS" and state.get("effective_status") == "COMPLETE", f"C/{scene}: reconciliation not complete")
        require(state.get("physical_rerun_required") is False and state.get("new_physical_measurement_invoked") is False, f"C/{scene}: unexpected replay")
        terminal = state["terminal_decision"]
        require(float(terminal["normalized_width"]) <= float(terminal["terminal_normalized_width"]), f"C/{scene}: width above threshold")
        rows.append({
            "algorithm": "C",
            "scene": scene,
            "status": "COMPLETE",
            "accepted_rounds": terminal["accepted_rounds"],
            "rejected_rounds": terminal["rejected_outer_attempts"],
            "outer_attempt": terminal["outer_attempt"],
            "normalized_width": terminal["normalized_width"],
            "terminal_reconciliation_without_replay": True,
            "evidence_path": str(path),
            "evidence_sha256": sha256(path),
        })

    require(len(rows) == 12, f"expected 12 rows, found {len(rows)}")
    require({(row["algorithm"], row["scene"]) for row in rows} == {(algorithm, scene) for algorithm in "ABC" for scene in SCENES}, "A/B/C scene matrix is incomplete")
    rows.sort(key=lambda row: ("ABC".index(row["algorithm"]), SCENES.index(row["scene"])))
    output = {
        "schema_version": "mysql_four_scene_abc_completion_audit_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "expected_matrix_size": 12,
        "complete_matrix_size": 12,
        "new_stability_validation_performed": False,
        "rows": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps({"status": "PASS", "output": str(OUTPUT), "sha256": sha256(OUTPUT), "rows": len(rows)}))


if __name__ == "__main__":
    main()
