#!/usr/bin/env python3
"""Counterbalanced TPC-H check including A final and A best client-QPS points."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/compare_mysql_tpch_abc_client_qps_after_bc_v1.py"
_source = PREDECESSOR.read_text(encoding="utf-8")


def _replace_once(old: str, new: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"comparison-v4 predecessor differs: {old!r}")
    _source = _source.replace(old, new)


_replace_once(
    "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch/run_state.json",
    "v1132_to_b_tpch_client_qps_frozen_outer2_resume_20260830_tpch/run_state.json",
)
_replace_once(
    "v1126_to_c_tpch_client_qps_disk2_normalized_20260830_tpch/run_state.json",
    "v1133_to_c_tpch_client_qps_timeout_rows_20260830_tpch/run_state.json",
)
_replace_once(
    'A_FINAL_DECISION = (\n'
    '    ROOT / "history/mysql_four_scene_20260809"\n'
    '    / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"\n'
    '    / "round_014/outer_decision.json"\n'
    ')',
    'A_FINAL_DECISION = (\n'
    '    ROOT / "history/mysql_four_scene_20260809"\n'
    '    / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"\n'
    '    / "round_014/outer_decision.json"\n'
    ')\n'
    'A_CLIENT_BEST_DECISION = (\n'
    '    ROOT / "history/mysql_four_scene_20260809"\n'
    '    / "v54_to_a_storage_compatible_20260823_attempt001_tpch"\n'
    '    / "round_001/outer_decision.json"\n'
    ')',
)
_replace_once(
    '        a_decision = load(A_FINAL_DECISION)\n',
    '        a_decision = load(A_FINAL_DECISION)\n'
    '        a_best_decision = load(A_CLIENT_BEST_DECISION)\n',
)
_replace_once(
    '            or (a_decision.get("accepted") or {}).get("decision", {}).get(\n'
    '                "accepted"\n'
    '            ) is not True\n',
    '            or (a_decision.get("accepted") or {}).get("decision", {}).get(\n'
    '                "accepted"\n'
    '            ) is not True\n'
    '            or (a_best_decision.get("accepted") or {}).get("decision", {}).get(\n'
    '                "accepted"\n'
    '            ) is not True\n'
    '            or float(\n'
    '                ((a_best_decision.get("accepted") or {}).get("row") or {}).get(\n'
    '                    "client_qps_audit"\n'
    '                ) or 0.0\n'
    '            ) <= 0.0\n',
)
_replace_once(
    '            "a": copy.deepcopy(dict(a_state["incumbent_params"])),\n',
    '            "a_final": copy.deepcopy(dict(a_state["incumbent_params"])),\n'
    '            "a_client_best": copy.deepcopy(dict(\n'
    '                a_best_decision["accepted"]["row"]["candidate_ranking"][\n'
    '                    "optimized_params"\n'
    '                ]\n'
    '            )),\n',
)
_replace_once(
    '            "algorithm_a_check_binding": {\n'
    '                "state_path": str(A_STATE),\n'
    '                "state_sha256": sha(A_STATE),\n'
    '                "decision_path": str(A_FINAL_DECISION),\n'
    '                "decision_sha256": sha(A_FINAL_DECISION),\n'
    '            },',
    '            "algorithm_a_check_binding": {\n'
    '                "state_path": str(A_STATE),\n'
    '                "state_sha256": sha(A_STATE),\n'
    '                "final_decision_path": str(A_FINAL_DECISION),\n'
    '                "final_decision_sha256": sha(A_FINAL_DECISION),\n'
    '                "client_best_decision_path": str(A_CLIENT_BEST_DECISION),\n'
    '                "client_best_decision_sha256": sha(A_CLIENT_BEST_DECISION),\n'
    '            },',
)
_replace_once(
    '            "counterbalanced_order": [\n'
    '                {"tpch_w1": "baseline", "tpch_w2": "c"},\n'
    '                {"tpch_w1": "a", "tpch_w2": "b"},\n'
    '                {"tpch_w1": "b", "tpch_w2": "a"},\n'
    '                {"tpch_w1": "c", "tpch_w2": "baseline"},\n'
    '            ],',
    '            "counterbalanced_order": [\n'
    '                {"tpch_w1": "baseline", "tpch_w2": "a_client_best"},\n'
    '                {"tpch_w1": "a_final", "tpch_w2": "b"},\n'
    '                {"tpch_w1": "b", "tpch_w2": "c"},\n'
    '                {"tpch_w1": "c", "tpch_w2": "baseline"},\n'
    '                {"tpch_w1": "a_client_best", "tpch_w2": "a_final"},\n'
    '            ],',
)
_replace_once(
    '                "a_final_decision": {\n'
    '                    "path": str(A_FINAL_DECISION),\n'
    '                    "sha256": sha(A_FINAL_DECISION),\n'
    '                },',
    '                "a_final_decision": {\n'
    '                    "path": str(A_FINAL_DECISION),\n'
    '                    "sha256": sha(A_FINAL_DECISION),\n'
    '                },\n'
    '                "a_client_best_decision": {\n'
    '                    "path": str(A_CLIENT_BEST_DECISION),\n'
    '                    "sha256": sha(A_CLIENT_BEST_DECISION),\n'
    '                },',
)
_replace_once(
    '        a_qps = float(summaries["a"]["mean_client_qps"])\n'
    '        for label in ("a", "b", "c"):\n',
    '        a_final_qps = float(summaries["a_final"]["mean_client_qps"])\n'
    '        a_best_qps = float(summaries["a_client_best"]["mean_client_qps"])\n'
    '        for label in ("a_final", "a_client_best", "b", "c"):\n',
)
_replace_once(
    '        b_beats_a = float(summaries["b"]["mean_client_qps"]) > a_qps\n'
    '        c_beats_a = float(summaries["c"]["mean_client_qps"]) > a_qps\n',
    '        b_beats_a_final = (\n'
    '            float(summaries["b"]["mean_client_qps"]) > a_final_qps\n'
    '        )\n'
    '        c_beats_a_final = (\n'
    '            float(summaries["c"]["mean_client_qps"]) > a_final_qps\n'
    '        )\n'
    '        b_beats_a_client_best = (\n'
    '            float(summaries["b"]["mean_client_qps"]) > a_best_qps\n'
    '        )\n'
    '        c_beats_a_client_best = (\n'
    '            float(summaries["c"]["mean_client_qps"]) > a_best_qps\n'
    '        )\n',
)
_replace_once(
    '            "schema_version": "mysql_tpch_abc_counterbalanced_client_qps_v1",\n'
    '            "status": "PASS" if b_beats_a and c_beats_a else "GOAL_NOT_MET",',
    '            "schema_version": "mysql_tpch_abc_counterbalanced_client_qps_v4",\n'
    '            "status": (\n'
    '                "PASS"\n'
    '                if b_beats_a_client_best and c_beats_a_client_best\n'
    '                else "GOAL_NOT_MET"\n'
    '            ),',
)
_replace_once(
    '            "b_beats_a": b_beats_a,\n'
    '            "c_beats_a": c_beats_a,\n'
    '            "reported_algorithm_a_server_qps": {',
    '            "b_beats_a_final": b_beats_a_final,\n'
    '            "c_beats_a_final": c_beats_a_final,\n'
    '            "b_beats_a_client_best": b_beats_a_client_best,\n'
    '            "c_beats_a_client_best": c_beats_a_client_best,\n'
    '            "algorithm_a_historical_best_client_qps_audit": float(\n'
    '                a_best_decision["accepted"]["row"]["client_qps_audit"]\n'
    '            ),\n'
    '            "reported_algorithm_a_server_qps": {',
)
_replace_once(
    '            "b_beats_a": b_beats_a,\n'
    '            "c_beats_a": c_beats_a,\n'
    '        }, sort_keys=True))',
    '            "b_beats_a_client_best": b_beats_a_client_best,\n'
    '            "c_beats_a_client_best": c_beats_a_client_best,\n'
    '        }, sort_keys=True))',
)
_replace_once(
    '"mysql_tpch_abc_counterbalanced_client_qps_state_v1"',
    '"mysql_tpch_abc_counterbalanced_client_qps_state_v4"',
)
_replace_once(
    '"mysql_tpch_abc_counterbalanced_launch_manifest_v1"',
    '"mysql_tpch_abc_counterbalanced_launch_manifest_v4"',
)

_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._compare_mysql_tpch_abc_client_qps_after_bc_v4_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
