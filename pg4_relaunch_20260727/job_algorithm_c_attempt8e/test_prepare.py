#!/usr/bin/env python3
"""Focused local tests for the attempt8e Algorithm C admission package."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import prepare


class PrepareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = prepare.read_json(prepare.CONTRACT_PATH)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        source_table = (
            prepare.PACKAGE_ROOT.parent.parent
            / self.contract["experience_table"]["path"]
        )
        self.experience_table = (
            self.workspace / self.contract["experience_table"]["path"]
        )
        self.experience_table.parent.mkdir(parents=True)
        self.experience_table.write_bytes(source_table.read_bytes())
        self.runner = self.workspace / "session_algorithm_c_runner.py"
        self.runner.write_text(
            "\n".join([
                'PARAMETER_APPLICATION = "SET LOCAL"',
                'READBACK_SQL = "SELECT current_setting($1)"',
                'SCOPE = "per_candidate per_worker immediate_readback"',
                'ORDER = "readback_before_workload rollback_after_candidate rollback_after_query"',
                'TRANSACTION = "BEGIN SET LOCAL READBACK WORKLOAD ROLLBACK"',
                "",
            ]),
            encoding="utf-8",
        )
        self.python = self.root / "python"
        self.python.write_text("", encoding="utf-8")
        self.base = self.root / "base.json"
        workload = self.contract["job_workload"]
        self.base.write_text(
            json.dumps({
                "benchmark": {"workload_cmd": "run pg job"},
                "parameter_application_contract": copy.deepcopy(
                    self.contract["parameter_application_contract"]
                ),
                "job_session_runner": {
                    "endpoint": {
                        "host": "127.0.0.1",
                        "port": 64311,
                        "database": workload["database"],
                        "user": "postgres",
                        "password_env": "PGPASSWORD"
                    },
                    "resource_samples_path": str(self.root / "resources.jsonl"),
                    "sql_dir": workload["sql_dir"],
                    "query_sequence": workload["query_sequence"],
                    "query_weights": workload["query_weights"],
                    "query_sha256": workload["query_sha256"]
                },
            }),
            encoding="utf-8",
        )
        self.r0 = self.root / "r0.json"
        profiles = []
        for expected in self.contract["r0_profiles"]:
            profiles.append({
                "passed": True,
                "status": "complete",
                "stable": True,
                "db_qps": expected["db_qps"],
                "memory_limit_bytes": expected["memory_limit_bytes"],
                "resource_utilization": copy.deepcopy(expected["resource_utilization"]),
            })
        self.r0.write_text(
            json.dumps({
                "status": "complete",
                "all_three_r0_passed": True,
                "profiles": profiles,
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, output: Path):
        outer = prepare.PACKAGE_ROOT / "job_algorithm_c_outer.py"
        session = prepare.PACKAGE_ROOT / "job_session_local_runner.py"
        sampler = prepare.PACKAGE_ROOT / "formal_cgroup_v1_sampler.py"
        fresh = prepare.PACKAGE_ROOT / "job_fresh_container.py"
        return type("Args", (), {
            "bundle_root": str(prepare.BUNDLE_ROOT),
            "output_dir": str(output),
            "base_config": str(self.base),
            "r0_summary": str(self.r0),
            "workspace": str(self.workspace),
            "python": str(self.python),
            "runner": str(outer),
            "application_source": [str(outer), str(session), str(sampler), str(fresh)],
        })()

    def test_prepare_is_idempotent_and_verifiable(self) -> None:
        output = self.root / "attempt"
        self.assertEqual(prepare.prepare(self.args(output)), 0)
        first = {name: (output / name).read_bytes() for name in prepare.GENERATED_FILES}
        self.assertEqual(prepare.prepare(self.args(output)), 0)
        second = {name: (output / name).read_bytes() for name in prepare.GENERATED_FILES}
        self.assertEqual(first, second)
        prepare.verify_prepared(output)

    def test_prepare_preserves_virtualenv_python_symlink(self) -> None:
        runtime = self.root / "runtime-python"
        runtime.symlink_to(self.python)
        args = self.args(self.root / "venv-attempt")
        args.python = str(runtime)
        self.assertEqual(prepare.prepare(args), 0)
        manifest = json.loads(
            (Path(args.output_dir) / "launch_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["python"], str(runtime.absolute()))

    def test_experience_table_sha_drift_fails_closed(self) -> None:
        value = json.loads(self.experience_table.read_text(encoding="utf-8"))
        value["table"]["seq_page_cost"]["memory"] = 99
        self.experience_table.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(prepare.GateError, "experience table T SHA256 mismatch"):
            prepare.prepare(self.args(self.root / "blocked"))

    def test_launcher_check_does_not_create_run_state(self) -> None:
        output = self.root / "attempt"
        prepare.prepare(self.args(output))
        result = subprocess.run(
            [
                "bash",
                str(prepare.PACKAGE_ROOT / "launch.sh"),
                "--prepared-dir",
                str(output),
                "--check",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHECK_ONLY", result.stdout)
        self.assertFalse((output / "run_state").exists())

    def test_prepare_accepts_packaged_algorithm_c_outer(self) -> None:
        args = self.args(self.root / "attempt")
        self.assertEqual(prepare.prepare(args), 0)
        manifest = prepare.read_json(
            Path(args.output_dir) / "launch_manifest.json"
        )
        self.assertEqual(Path(manifest["runner"]), Path(args.runner).resolve())
        self.assertEqual(
            manifest["experience_table_sha256"],
            self.contract["experience_table"]["sha256"],
        )
        self.assertFalse(
            {"dataset_path", "dataset_sha256", "ranker_path", "ranker_sha256"}
            .intersection(manifest)
        )
        live = prepare.read_json(Path(args.output_dir) / "live.json")
        self.assertEqual(prepare.legacy_dependency_keys(live), set())
        self.assertNotIn("gradient", live["optimizer"])
        self.assertNotIn(
            "dataset_sha256",
            self.contract["algorithm_c"]["required_gates"],
        )
        self.assertNotIn(
            "ranker_sha256",
            self.contract["algorithm_c"]["required_gates"],
        )

    def test_base_fragment_matches_frozen_contract(self) -> None:
        fragment = prepare.read_json(
            prepare.PACKAGE_ROOT / "job_session_runner_base_fragment.json"
        )
        self.assertEqual(
            fragment["parameter_application_contract"],
            self.contract["parameter_application_contract"],
        )
        workload = self.contract["job_workload"]
        session = fragment["job_session_runner"]
        for key in ("sql_dir", "query_sequence", "query_weights", "query_sha256"):
            self.assertEqual(session[key], workload[key])
        self.assertEqual(session["endpoint"]["database"], workload["database"])

    def test_r0_drift_fails_closed(self) -> None:
        value = json.loads(self.r0.read_text(encoding="utf-8"))
        value["profiles"][0]["db_qps"] += 0.01
        self.r0.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(prepare.GateError, "QPS drift"):
            prepare.prepare(self.args(self.root / "blocked"))

    def test_alter_system_application_path_fails_closed(self) -> None:
        self.runner.write_text(
            self.runner.read_text(encoding="utf-8") + 'SQL = "ALTER SYSTEM SET work_mem"\n',
            encoding="utf-8",
        )
        args = self.args(self.root / "blocked")
        args.application_source.append(str(self.runner))
        with self.assertRaisesRegex(prepare.GateError, "prohibited parameter application path"):
            prepare.prepare(args)

    def test_base_parameter_application_semantics_drift_fails_closed(self) -> None:
        base = json.loads(self.base.read_text(encoding="utf-8"))
        base["parameter_application_contract"]["method"] = "ALTER_SYSTEM"
        self.base.write_text(json.dumps(base), encoding="utf-8")
        with self.assertRaisesRegex(prepare.GateError, "parameter application semantics drift"):
            prepare.prepare(self.args(self.root / "blocked"))

    def test_undeclared_local_application_dependency_fails_closed(self) -> None:
        unsafe = self.workspace / "unsafe_applier.py"
        unsafe.write_text('SQL = "ALTER SYSTEM SET work_mem"\n', encoding="utf-8")
        self.runner.write_text(
            "import unsafe_applier\n" + self.runner.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        args = self.args(self.root / "blocked")
        args.application_source.append(str(self.runner))
        with self.assertRaisesRegex(prepare.GateError, "undeclared local application dependency"):
            prepare.prepare(args)


if __name__ == "__main__":
    unittest.main()
