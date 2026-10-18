#!/usr/bin/env python3
"""Offline regression tests for the JOB v55 replay transaction boundary."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
V54 = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02"
    / "formal_job_outer_replay_v54.py"
)
V55 = V54.with_name("formal_job_outer_replay_v55.py")
BASE_V10 = ROOT / "tools/run_job_round8_strict_serial_base_v10_sealed_explain.py"


def load_v55():
    spec = importlib.util.spec_from_file_location("formal_job_outer_replay_v55", V55)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def calls_in_main(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    result = []
    for node in ast.walk(main):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            result.append(node.func.id)
    return result


def test_v54_seed_was_uncounted_real_query_execution() -> None:
    source = V54.read_text(encoding="utf-8")
    assert '"-f", str(sql_path)' in source
    assert source.index('atomic_json(output / "point_result.json"') < source.index(
        "seed_control_plan_statements()", source.index("def main()")
    )


def test_v55_never_calls_postrun_seed() -> None:
    assert "seed_control_plan_statements" not in calls_in_main(V55)
    source = V55.read_text(encoding="utf-8")
    costs = source.index("costs, cost_source = resolve_replay_costs()")
    binding = source.index(
        "sealed_explain_binding = validate_preexecution_sealed_explain_binding(costs)"
    )
    claim = source.index("replay_accounting = claim_replay_accounting()")
    assert costs < binding < claim


def test_12a_disconnect_cannot_reclassify_sealed_core_measurement() -> None:
    module = load_v55()
    failure = RuntimeError(
        "psql: 12a.sql: server closed the connection unexpectedly"
    )
    classified = module.classify_legacy_postrun_seed_failure(
        core_measurement_sealed=True,
        operation="legacy_postrun_pg_stat_statements_seed",
        error=failure,
    )
    assert classified["core_measurement_status"] == "valid"
    assert classified["physical_invocation_technical_failure"] is False
    assert classified["repeat_query_count"] == 0
    try:
        module.classify_legacy_postrun_seed_failure(
            core_measurement_sealed=False,
            operation="legacy_postrun_pg_stat_statements_seed",
            error=failure,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("pre-seal failure must remain technical")
    try:
        module.classify_legacy_postrun_seed_failure(
            core_measurement_sealed=True,
            operation="formal_measurement",
            error=failure,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("real measurement failure must remain technical")


def test_binding_requires_exact_preexecution_fingerprint() -> None:
    module = load_v55()
    costs = {
        "cpu_index_tuple_cost": 0.0005,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 7000.0,
        "parallel_tuple_cost": 0.001,
        "random_page_cost": 4.0,
        "seq_page_cost": 1.0,
    }
    canonical = json.dumps(costs, sort_keys=True, separators=(",", ":"))
    identifier = "three-level-sha256:" + "a" * 64
    fingerprint = {
            "id": identifier,
            "plan_level": "plan-sha256:" + "c" * 64,
    }
    previous = os.environ.get("RA_PG_SEALED_EXPLAIN_BINDING_JSON")
    with tempfile.TemporaryDirectory() as directory:
        selection_path = Path(directory) / "top5.json"
        selection_path.write_text(
            json.dumps(
                {
                    "top5": [
                        {
                            "candidate_params": costs,
                            "boundary_corner": False,
                            "three_level_fingerprint": fingerprint,
                        }
                    ]
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        binding = {
            "contract": "job_v55_preexecution_sealed_explain_binding_v1",
            "selection_path": str(selection_path),
            "selection_sha256": hashlib.sha256(
                selection_path.read_bytes()
            ).hexdigest(),
            "candidate_parameter_sha256": hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            "fingerprint_id": identifier,
            "plan_level": "plan-sha256:" + "c" * 64,
            "three_level_fingerprint": fingerprint,
            "post_run_pg_stat_statements_seed_used": False,
        }
        try:
            os.environ["RA_PG_SEALED_EXPLAIN_BINDING_JSON"] = json.dumps(binding)
            assert module.validate_preexecution_sealed_explain_binding(costs) == binding
            binding["candidate_parameter_sha256"] = "0" * 64
            os.environ["RA_PG_SEALED_EXPLAIN_BINDING_JSON"] = json.dumps(binding)
            try:
                module.validate_preexecution_sealed_explain_binding(costs)
            except RuntimeError:
                pass
            else:
                raise AssertionError("mismatched parameter binding must fail preclaim")
        finally:
            if previous is None:
                os.environ.pop("RA_PG_SEALED_EXPLAIN_BINDING_JSON", None)
            else:
                os.environ["RA_PG_SEALED_EXPLAIN_BINDING_JSON"] = previous


def test_runner_derives_binding_from_sealed_selection_before_workload() -> None:
    source = BASE_V10.read_text(encoding="utf-8")
    assert "selection_path = Path(sys.argv[1]).resolve()" in source
    assert 'candidates = selection.get("top5")' in source
    assert "if len(matches) != 1:" in source
    assert 'candidate.get("three_level_fingerprint")' in source
    binding_call = source.index("_sealed_explain_binding(_ACTIVE_COSTS)")
    environment_return = source.index("return environment", binding_call)
    assert binding_call < environment_return


def main() -> int:
    test_v54_seed_was_uncounted_real_query_execution()
    test_v55_never_calls_postrun_seed()
    test_12a_disconnect_cannot_reclassify_sealed_core_measurement()
    test_binding_requires_exact_preexecution_fingerprint()
    test_runner_derives_binding_from_sealed_selection_before_workload()
    print(
        json.dumps(
            {
                "status": "passed",
                "contract": "job_v55_postrun_seed_boundary_regression_v1",
                "tests": 5,
                "workload_calls": 0,
                "ssh_calls": 0,
                "mysql_touched": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
