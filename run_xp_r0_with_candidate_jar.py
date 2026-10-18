from __future__ import annotations

import os
import sys
from pathlib import Path
import json

from host02_job_xp_mutex import mutex_from_campaign
from mysql_algorithm_c_production_services import build
import shell.manual_mysql_sgd_r0_campaign as campaign


campaign.HYBENCH_JAR = Path(
    "/home/anonymous/program/resource_adaptive_mysql/formal_hybench_src_20260723/target/"
    "HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-jar-with-dependencies.jar"
)

try:
    run_id = sys.argv[sys.argv.index("--run-id") + 1]
except (ValueError, IndexError):
    raise SystemExit("--run-id is required for trace capture")
trace_dir = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/mysql_sgd_env_agents_20260723/xp/formal_sgd_campaigns"
) / run_id
os.environ["FORMAL_MYSQL_BINDING_TRACE_PATH"] = str(trace_dir / "formal_binding_trace.jsonl")
os.environ["FORMAL_MYSQL_BINDING_TRACE_RUN_ID"] = run_id
os.environ["FORMAL_MYSQL_BINDING_TRACE_SCHEMA"] = "formal_mysql_binding_trace_event_v1"

if __name__ == "__main__":
    config_path = Path(
        "/home/anonymous/program/resource_adaptive_mysql/config/formal/"
        "mysql_sgd_production/hybench_xp_io.algorithm_c.optimizer.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    bundle = build(config)
    mutex = mutex_from_campaign(config, bundle, "hybench_xp")
    if mutex is None:
        raise SystemExit("XP R0 requires the host02 full-lifecycle mutex")
    with mutex:
        raise SystemExit(campaign.main())
