#!/usr/bin/env python3
"""Regression checks for VM teacher workload profile plumbing."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import run_pg_benchmark_bottleneck_matrix as runner


def parse_args(extra: list[str]) -> object:
    old_argv = sys.argv
    try:
        sys.argv = ["run_pg_benchmark_bottleneck_matrix.py", *extra]
        return runner.parse_args()
    finally:
        sys.argv = old_argv


def test_statement_profile_and_setup_sql() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-teacher-profile-") as temp_dir:
        root = Path(temp_dir)
        base = root / "base.toml"
        base.write_text(
            '[AP-6]\nsql = """select 1;"""\n\n'
            '[AP-8]\nsql = """select 8;"""\n',
            encoding="utf-8",
        )
        override = root / "ap6.sql"
        override.write_text("select count(*) from transfer;\n", encoding="utf-8")
        output = root / "generated" / "statements.toml"

        generated = runner.build_hybench_statement_profile(
            base_path=base,
            output_path=output,
            raw_overrides=[f"AP-6={override}"],
        )
        text = generated.read_text(encoding="utf-8")
        assert generated == output
        assert "select count(*) from transfer;" in text
        assert "select 8;" in text
        assert "select 1;" in base.read_text(encoding="utf-8")

        setup = root / "setup.sql"
        setup.write_text("VACUUM (ANALYZE) transfer;\n", encoding="utf-8")
        sql_items, paths = runner.load_hybench_setup_sql([str(setup)])
        assert sql_items == ["VACUUM (ANALYZE) transfer;"]
        assert paths == [str(setup.resolve())]


def test_parse_args_teacher_options() -> None:
    args = parse_args(
        [
            "--bonect-memory-use-xp",
            "--hybench-statement-sql-file",
            "AP-6=shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v9.sql",
            "--hybench-setup-sql-file",
            "shell/hybenchxp_memory_groupagg_setup.sql",
        ]
    )

    assert args.bonect_memory_use_xp is True
    assert args.hybench_statement_sql_file == [
        "AP-6=shell/hybenchxp_memory_ap6_transfer_groupagg_switch_v9.sql"
    ]
    assert args.hybench_setup_sql_file == [
        "shell/hybenchxp_memory_groupagg_setup.sql"
    ]
    assert args.validation_warmup_drain_timeout_sec == 90.0
    assert args.validation_warmup_drain_poll_sec == 2.0
    assert args.validation_warmup_drain_max_app_connections == 0
    assert (
        args.validation_warmup_drain_application_name
        == "resource-adaptive-matrix"
    )


def test_blind_teacher_config_clears_historical_seeds() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-teacher-config-") as temp_dir:
        root = Path(temp_dir)
        args = parse_args(
            [
                "--scenario-set",
                "bonect",
                "--bonect-bottlenecks",
                "cpu",
                "--use-resource-estimator",
                "--generated-config-dir",
                str(root / "generated"),
                "--output-dir",
                str(root / "history"),
                "--phase2-teacher-recovery-config",
                "config/bonect_phase2_vm_teacher_recovery_20260711.json",
            ]
        )
        spec = runner.CommandSpec(
            benchmark="hybench_xp",
            bottleneck="cpu",
            command=[],
            cwd=Path(runner.PROJECT_ROOT),
            env={},
            log_path=root / "cpu.log",
            pg_dbname="hybench_sf10x",
        )
        config_path = runner.build_optimizer_config(
            spec,
            args,
            "phase2_teacher_config_test",
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))

    bruteforce = config["optimizer"]["bruteforce"]
    audit = bruteforce["teacher_recovery_audit"]
    assert audit["label"] == "final_audit_cpu_vm_teacher", audit
    assert audit["blind_holdout"] is True, audit
    assert bruteforce["force_best_replay_anchor_seed_records"] == [], bruteforce
    gate = bruteforce["candidate_gate"]
    assert gate["allow_known_real_direction_seed_gate_bypass"] is False, gate
    assert gate["known_real_seed_prefix_reserve"]["enabled"] is False, gate
    assert gate["feedback_rejected_neighborhoods"] == [], gate
    benchmark = config["benchmark"]
    assert benchmark["validation_warmup_drain_timeout_sec"] == 90.0
    assert benchmark["validation_warmup_drain_poll_sec"] == 2.0
    assert benchmark["validation_warmup_drain_max_app_connections"] == 0
    assert (
        benchmark["validation_warmup_drain_application_name"]
        == "resource-adaptive-matrix"
    )
    cpu_model = config["metrics"]["plan_estimator"]["cpu"]["learned_model"]
    assert cpu_model == {
        "enabled": True,
        "path": "config/generated/cpu_resource_models.json",
        "model_set": "C_only",
        "required": True,
    }
    assert (
        config["metrics"]["plan_estimator"]["logging"][
            "candidate_probe_compact_logging"
        ]
        is True
    )


def main() -> int:
    test_statement_profile_and_setup_sql()
    test_parse_args_teacher_options()
    test_blind_teacher_config_clears_historical_seeds()
    print("phase2 HyBench teacher profile tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
