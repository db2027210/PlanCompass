from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config/formal/mysql_sgd_production/hybench_xp_io.algorithm_c.optimizer.json"
CONTRACT = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/hybench_xp.production_binding_capture_contract_v1.json"
TABLE = ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/T.hybench_xp_io.operator_physics_v1.json"


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    table_hash = hashlib.sha256(TABLE.read_bytes()).hexdigest()
    queries = contract.get("queries")
    query_ids = []
    representative_binding_ok = False
    if isinstance(queries, list):
        query_ids = [str(q.get("query_id") or q.get("statement_id")) for q in queries if isinstance(q, dict)]
        representative_binding_ok = bool(queries) and all(
            (not q.get("dynamic")) or bool(q.get("representative_bindings"))
            for q in queries if isinstance(q, dict)
        )
    report = {
        "config_path": str(CONFIG),
        "contract_path": str(CONTRACT),
        "table_sha256": table_hash,
        "table_sha256_canonical": table_hash == "7f3077c3f4ea870409643a2674e908052e88878efe522f8c0e376911e410fbbb",
        "config_port": config.get("mysql", {}).get("port"),
        "config_port_ok": config.get("mysql", {}).get("port") == 65104,
        "method_id": config.get("method_id"),
        "profile": config.get("profile"),
        "output_directory": config.get("output", {}).get("directory"),
        "top5_ok": (config.get("formal_outer_protocol", {}).get("formal_search_initial_replay_top_k") == 5
                    and config.get("formal_outer_protocol", {}).get("formal_search_max_replay_top_k") == 5),
        "qps_outer_only_contract": "qps" not in json.dumps({"optimizer": config.get("optimizer"), "scoring": config.get("scoring")}).lower(),
        "contract_execution_authorized": contract.get("execution_authorized"),
        "contract_strict_cluster_usable": contract.get("strict_plan_decision", {}).get("strict_cluster_usable_now"),
        "contract_raw_explain_preexisting": contract.get("current_implementation_gap", {}).get("xp_raw_explain_preexisting"),
        "query_id_count": len(query_ids),
        "query_ids_unique": len(query_ids) == len(set(query_ids)),
        "representative_bindings_complete": representative_binding_ok,
        "raw_explain_command_template": "EXPLAIN FORMAT=JSON <one observed actual SQL statement>",
        "preflight_ready_for_pressure": False,
    }
    evidence_path = ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/hybench_xp_io/xp_static_preflight.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
