from __future__ import annotations

import json
import csv
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from mysql_workload_adapter import (
    MySQLEndpoint, build_job_manifest, generate_benchbase_xml,
    generate_hybench_properties, parse_benchbase_summary, parse_hybench_stdout,
    parse_hybench_formal_metrics, parse_hybench_mode_metrics,
    run_benchbase, run_hybench, run_job,
)


ENDPOINT = MySQLEndpoint("127.0.0.1", 3307, "bench", "runner", "secret")


class AdapterTest(unittest.TestCase):
    def test_benchbase_xml_is_private_and_summary_units(self):
        with tempfile.TemporaryDirectory() as directory:
            weights = [1 if index in {1, 15} else 0 for index in range(1, 23)]
            path = generate_benchbase_xml("tpch", ENDPOINT, directory, duration_s=12,
                                          terminals=3, transaction_weights=weights)
            root = ET.parse(path).getroot()
            self.assertEqual(root.findtext("url"), "jdbc:mysql://127.0.0.1:3307/bench?useServerPrepStmts=true&allowLoadLocalInfile=true")
            self.assertEqual(root.findtext("works/work/time"), "12")
            self.assertEqual(root.findtext("afterload"), "/benchmarks/tpch/postload-mysql.sql")
            self.assertIsNone(root.findtext("works/work/serial"))
            self.assertEqual(root.findtext("works/work/weights"),
                             ",".join(str(value) for value in weights))
            all_queries = generate_benchbase_xml("tpch", ENDPOINT, Path(directory) / "all",
                                                 duration_s=12, terminals=3)
            self.assertEqual(ET.parse(all_queries).getroot().findtext("works/work/serial"), "true")
            complete = generate_benchbase_xml(
                "tpch", ENDPOINT, Path(directory) / "complete", terminals=1,
                complete_query_set=True,
            )
            complete_root = ET.parse(complete).getroot()
            self.assertEqual(complete_root.findtext("works/work/serial"), "true")
            self.assertIsNone(complete_root.find("works/work/time"))
            reverse = generate_benchbase_xml(
                "tpch", ENDPOINT, Path(directory) / "reverse", terminals=1,
                complete_query_set=True, query_order=list(range(22, 0, -1)),
            )
            reverse_types = ET.parse(reverse).getroot().findall(
                "transactiontypes/transactiontype"
            )
            self.assertEqual(reverse_types[0].findtext("name"), "Q22")
            self.assertIsNone(reverse_types[0].find("id"))
            subset = generate_benchbase_xml(
                "tpch", ENDPOINT, Path(directory) / "subset", terminals=1,
                complete_query_set=True, query_order=[2, 22, 17],
            )
            subset_types = ET.parse(subset).getroot().findall(
                "transactiontypes/transactiontype"
            )
            self.assertEqual(
                [node.findtext("name") for node in subset_types],
                ["Q2", "Q22", "Q17"],
            )
            self.assertTrue(all(node.find("id") is None for node in subset_types))
            with self.assertRaisesRegex(ValueError, "exactly one terminal"):
                generate_benchbase_xml(
                    "tpch", ENDPOINT, Path(directory) / "bad", terminals=2,
                    complete_query_set=True,
                )
        metrics = parse_benchbase_summary({"Throughput (requests/second)": 10.5,
            "Latency Distribution": {"95th Percentile Latency (microseconds)": 2500,
                                     "99th Percentile Latency (microseconds)": 9000}})
        self.assertEqual((metrics.qps, metrics.latency_p95_ms, metrics.latency_p99_ms), (10.5, 2.5, 9.0))

    def test_default_dry_runs_never_call_runner(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("dry-run launched a process")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bb = run_benchbase("tpch", ENDPOINT, root / "bb", jar_path=root / "missing.jar", runner=forbidden)
            hy = run_hybench("ap", ENDPOINT, root / "hy", hybench_home=root / "missing", runner=forbidden)
            sql = root / "q.sql"; sql.write_text("SELECT 1;", encoding="utf-8")
            manifest = build_job_manifest([sql], root / "manifest")
            job = run_job(manifest, ENDPOINT, root / "job", runner=forbidden)
            self.assertTrue(bb["dry_run"] and hy["dry_run"] and job["dry_run"])

    def test_hybench_properties_and_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            props = generate_hybench_properties(ENDPOINT, directory, extra={"sf": .1})
            text = props.read_text()
            self.assertIn("url=jdbc:mysql://127.0.0.1:3307/bench", text)
            self.assertIn("sf=0.1", text)
        metrics = parse_hybench_stdout("warmup QPS=1\nThroughput: 52.5\nP95: 1200 us P99=3.5 ms")
        self.assertEqual((metrics.qps, metrics.latency_p95_ms, metrics.latency_p99_ms), (52.5, 1.2, 3.5))
        actual = parse_hybench_stdout(
            "QPS is 12.5\nAP Query 1 : 95% rt : 8.0 ms | 99% rt : 9.0 ms\n"
            "AP Query 2 : 95% rt : 10.0 ms | 99% rt : 11.0 ms"
        )
        self.assertEqual((actual.qps, actual.latency_p95_ms, actual.latency_p99_ms),
                         (12.5, 10.0, 11.0))
        xp = parse_hybench_mode_metrics(
            "QPS is 8\nTPS is 9\nXP-QPS is 3.5\nXP-TPS is 4.5\n"
            "Interative Query 1 : 95% rt : 7.0 ms | 99% rt : 8.0 ms", "xp"
        )
        self.assertEqual(xp.qps, 3.5)
        xp_json = parse_hybench_mode_metrics(
            "Interative Query 1 : 95% rt : 7.0 ms | 99% rt : 8.0 ms\n"
            "HYBENCH_METRICS_JSON={\"qps\":5.25,\"query_count\":100}\n",
            "xp",
        )
        self.assertEqual(xp_json.qps, 5.25)

    def test_native_xp_properties_fail_closed_on_workload_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = generate_hybench_properties(
                ENDPOINT, root / "native", mode="xp",
                extra={"sf": "10x", "xapclient": 16, "xtpclient": 16},
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("xapclient=16\n", text)
            self.assertIn("xtpclient=16\n", text)
            self.assertIn("at1_percent=35\n", text)
            self.assertIn("at6_percent=3\n", text)
            for forbidden in (
                {"ap_query_ids": "1,2,3"},
                {"ap_query_weights": "1,1,1,1,1,1,1"},
                {"ap_query_sequence": "1,2,3"},
                {"ap_query_sequence_limit": 7},
                {"at1_percent": 34},
                {"tp1_percent": 10},
                {"fresh_percent": 40},
            ):
                with self.assertRaisesRegex(ValueError, "forbids"):
                    generate_hybench_properties(
                        ENDPOINT, root / "reject", mode="xp", extra=forbidden,
                    )
            with self.assertRaisesRegex(ValueError, "together"):
                generate_hybench_properties(
                    ENDPOINT, root / "one-sided", mode="xp",
                    extra={"xapclient": 8},
                )
            with self.assertRaisesRegex(ValueError, "1:1"):
                generate_hybench_properties(
                    ENDPOINT, root / "ratio", mode="xp",
                    extra={"xapclient": 8, "xtpclient": 16},
                )

    def test_native_xp_binds_sealed_runtime_jar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "bundle" / "runtime" / "native.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"sealed-native-iq1-iq7")
            (jar.parent.parent / "manifest.json").write_text(json.dumps({
                "execution_authorized": True,
                "runtime_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            plan = run_hybench(
                "xp", ENDPOINT, root / "run", hybench_home=root / "missing",
                runtime_jar=jar, dry_run=True,
            )
            self.assertEqual(plan["runtime_jar"], str(jar.resolve()))
            self.assertTrue(plan["runtime_jar_sha256"])
            with self.assertRaisesRegex(ValueError, "sealed runtime JAR"):
                run_hybench(
                    "xp", ENDPOINT, root / "reject", hybench_home=root / "missing",
                    dry_run=True,
                )
            authority = jar.parent.parent / "manifest.json"
            authority.write_text(json.dumps({
                "execution_authorized": False,
                "runtime_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not execution-authorized"):
                run_hybench(
                    "xp", ENDPOINT, root / "unauthorized", hybench_home=root / "missing",
                    runtime_jar=jar, dry_run=True,
                )

    def test_adaptive_native_xp_binds_wrapper_and_300_to_900_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "bundle" / "runtime" / "native.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"sealed-native")
            (jar.parent.parent / "manifest.json").write_text(json.dumps({
                "execution_authorized": True,
                "runtime_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            wrapper = root / "adaptive.jar"
            wrapper.write_bytes(b"adaptive-wrapper")
            plan = run_hybench(
                "xp", ENDPOINT, root / "run", hybench_home=root / "missing",
                duration_s=900, runtime_jar=jar,
                formal_control={
                    "runner_jar": wrapper,
                    "stop_file": root / "run" / "stop.txt",
                },
                dry_run=True,
            )
            self.assertEqual(plan["argv"][3], "com.hybench.HyBenchAdaptiveMain")
            properties = Path(plan["properties"]).read_text(encoding="utf-8")
            self.assertIn("formal_minimum_measurement_seconds=300\n", properties)
            self.assertIn("formal_maximum_measurement_seconds=900\n", properties)
            with self.assertRaisesRegex(ValueError, "300..900"):
                run_hybench(
                    "xp", ENDPOINT, root / "reject", hybench_home=root / "missing",
                    duration_s=600, runtime_jar=jar,
                    formal_control={"runner_jar": wrapper, "stop_file": root / "reject.stop"},
                    dry_run=True,
                )

    def test_parse_adaptive_boundary_metrics(self):
        metrics, formal = parse_hybench_formal_metrics(
            'HYBENCH_FORMAL_METRICS_JSON={"xap_qps":2.5,'
            '"latency_p95_ms":7.0,"latency_p99_ms":9.0,'
            '"measurement_seconds":300}\n'
        )
        self.assertEqual(metrics.qps, 2.5)
        self.assertEqual(formal["measurement_seconds"], 300)

    def test_live_native_xp_v2_requires_and_accepts_iq1_iq7(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            (home / "lib").mkdir(parents=True)
            (home / "Data_1x").mkdir()
            (home / "conf").mkdir()
            (home / "conf" / "log4j2.properties").write_text("", encoding="utf-8")
            (home / "conf" / "stmt_mysql.toml").write_text("", encoding="utf-8")

            jar = root / "bundle" / "runtime" / "native-v2.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"native-iq1-iq7-v2")
            (jar.parent.parent / "manifest.json").write_text(json.dumps({
                "schema_version": "mysql_xp_native_iq1_iq7_successor_v2",
                "execution_authorized": True,
                "runtime_jar_sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
                "workload_semantics": {
                    "iq_selection": "uniform_random_IQ1_IQ7",
                    "exclusive_random_range": [1, 8],
                },
            }), encoding="utf-8")

            iq_lines = "\n".join(
                f"Interative Query {value} : 95% rt : 3 ms | 99% rt : 4 ms"
                for value in range(1, 8)
            )
            at_lines = "\n".join(
                f"Analytical Transaction AT{value} : 95% rt : 3 ms | 99% rt : 4 ms"
                for value in range(1, 7)
            )

            def fake(argv, **kwargs):
                return subprocess.CompletedProcess(
                    argv, 0,
                    f"{iq_lines}\n{at_lines}\nXP-QPS is 5.25\n", "",
                )

            output = root / "run"
            result = run_hybench(
                "xp", ENDPOINT, output, hybench_home=home,
                runtime_jar=jar, dry_run=False, runner=fake,
            )
            coverage = result["workload_coverage"]
            self.assertTrue(coverage["coverage_complete"])
            self.assertEqual(coverage["required_iq"], [f"IQ{i}" for i in range(1, 8)])
            self.assertEqual(coverage["completed_iq"], coverage["required_iq"])
            self.assertEqual(coverage["completed_at"], coverage["required_at"])
            self.assertEqual(coverage["unexpected_iq"], [])

    def test_live_hybench_uses_private_runtime_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"
            (home / "lib").mkdir(parents=True)
            (home / "Data_1x").mkdir()
            (home / "conf").mkdir()
            (home / "conf" / "log4j2.properties").write_text(
                "appender.error.fileName = hybench-error.log\n", encoding="utf-8")
            (home / "conf" / "stmt_mysql.toml").write_text(
                'sql = "SELECT * FROM CUSTOMER JOIN SAVINGACCOUNT ON 1=1"\n',
                encoding="utf-8")
            (home / "hybench").write_text("#!/bin/sh\n", encoding="utf-8")
            calls = []
            def fake(argv, **kwargs):
                calls.append(kwargs["cwd"])
                return subprocess.CompletedProcess(argv, 0,
                    "QPS is 2\nAP Query 1 : 95% rt : 3 ms | 99% rt : 4 ms\n", "")
            output = root / "run"
            result = run_hybench("ap", ENDPOINT, output, hybench_home=home,
                                 dry_run=False, runner=fake)
            self.assertEqual(result["metrics"]["qps"], 2)
            self.assertEqual(calls, [str(output / "hybench_runtime")])
            self.assertTrue((output / "hybench_runtime" / "lib").is_symlink())
            self.assertFalse((home / "hybench-error.log").exists())
            statements = (output / "stmt_mysql_case_normalized.toml").read_text()
            self.assertIn("customer JOIN savingAccount", statements)
            self.assertIn(str(output / "stmt_mysql_case_normalized.toml"), result["argv"])

    def test_live_hybench_rejects_hidden_sql_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"
            (home / "lib").mkdir(parents=True)
            (home / "Data_1x").mkdir()
            (home / "conf").mkdir()
            (home / "conf" / "log4j2.properties").write_text("", encoding="utf-8")
            (home / "conf" / "stmt_mysql.toml").write_text("", encoding="utf-8")
            (home / "hybench").write_text("#!/bin/sh\n", encoding="utf-8")
            def fake(argv, **kwargs):
                return subprocess.CompletedProcess(
                    argv, 0,
                    "QPS is 2\nAP Query 1 : 95% rt : 3 ms | 99% rt : 4 ms\n",
                    "java.sql.SQLSyntaxErrorException: missing table",
                )
            with self.assertRaisesRegex(RuntimeError, "SQL/JDBC exception"):
                run_hybench("ap", ENDPOINT, root / "run", hybench_home=home,
                             dry_run=False, runner=fake)

    def test_live_hybench_rejects_missing_workload_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"
            (home / "lib").mkdir(parents=True)
            (home / "Data_1x").mkdir()
            (home / "conf").mkdir()
            (home / "conf" / "log4j2.properties").write_text("", encoding="utf-8")
            (home / "conf" / "stmt_mysql.toml").write_text("", encoding="utf-8")
            (home / "hybench").write_text("#!/bin/sh\n", encoding="utf-8")

            def fake(argv, **kwargs):
                return subprocess.CompletedProcess(
                    argv, 0,
                    "QPS is 2\nAP Query 1 : 95% rt : 3 ms | 99% rt : 4 ms\n",
                    "java.lang.NoSuchMethodException: com.hybench.workload.APClient.execIQ7(java.sql.Connection)",
                )

            with self.assertRaisesRegex(RuntimeError, "missing workload method"):
                run_hybench("ap", ENDPOINT, root / "run", hybench_home=home,
                             dry_run=False, runner=fake)

    def test_job_manifest_tamper_and_live_replay_explain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sql = root / "q1.sql"; sql.write_text("SELECT 1;", encoding="utf-8")
            manifest = build_job_manifest([sql], root / "manifest")
            calls = []
            def fake(argv, **kwargs):
                calls.append((argv, kwargs["input"]))
                stdout = '{"query_block": {}}' if kwargs["input"].startswith("EXPLAIN") else "1"
                return subprocess.CompletedProcess(argv, 0, stdout, "")
            result = run_job(manifest, ENDPOINT, root / "run", dry_run=False,
                             concurrency=2, repetitions=2, runner=fake,
                             estimated_duration_s={"q1.sql": 10})
            self.assertGreater(result["metrics"]["qps"], 0)
            self.assertEqual(len(result["metrics"]["explain_samples"]), 1)
            self.assertEqual(len(calls), 3)
            self.assertTrue(result["query_coverage"]["coverage_complete"])
            self.assertEqual(result["query_coverage"]["completed_queries"], ["q1.sql"])
            self.assertEqual(result["scheduling_policy"], "shortest_estimated_first")
            self.assertNotIn("secret", " ".join(calls[0][0]))
            self.assertEqual(
                calls[0][0][1:4],
                ["--no-defaults", "--get-server-public-key", "--ssl-mode=DISABLED"],
            )
            sql.write_text("DELETE FROM t", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_job(manifest, ENDPOINT, root / "run2")

    def test_job_quotes_mysql_reserved_output_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sql = root / "q.sql"
            sql.write_text("SELECT MIN(name) AS character FROM names;", encoding="utf-8")
            manifest = build_job_manifest([sql], root / "manifest")
            calls = []

            def fake(argv, **kwargs):
                calls.append(kwargs["input"])
                stdout = '{"query_block": {}}' if kwargs["input"].startswith("EXPLAIN") else "x"
                return subprocess.CompletedProcess(argv, 0, stdout, "")

            run_job(manifest, ENDPOINT, root / "run", dry_run=False, runner=fake)
            self.assertEqual(calls[0], "SELECT MIN(name) AS `character` FROM names;")
            self.assertTrue(calls[1].startswith("EXPLAIN FORMAT=JSON SELECT MIN(name) AS `character`"))

    def test_job_rejects_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.sql"; path.write_text("UPDATE t SET x=1", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_job_manifest([path], Path(directory) / "out")
            path.write_text("SELECT 1; DROP TABLE t", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_job_manifest([path], Path(directory) / "out2")

    def test_tpch_complete_query_set_validates_raw_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "benchbase.jar"
            jar.write_bytes(b"fake")

            def fake(argv, **kwargs):
                output = Path(argv[argv.index("-d") + 1])
                summary = {
                    "Throughput (requests/second)": 1.0,
                    "Latency Distribution": {
                        "95th Percentile Latency (microseconds)": 1000,
                        "99th Percentile Latency (microseconds)": 2000,
                    },
                }
                (output / "tpch.summary.json").write_text(json.dumps(summary))
                with (output / "tpch.raw.csv").open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["Transaction Name"])
                    writer.writeheader()
                    writer.writerows({"Transaction Name": f"Q{i}"} for i in range(1, 23))
                return subprocess.CompletedProcess(argv, 0, "", "")

            result = run_benchbase(
                "tpch", ENDPOINT, root / "run", jar_path=jar, terminals=1,
                complete_query_set=True, dry_run=False, runner=fake,
            )
            self.assertTrue(result["query_coverage"]["coverage_complete"])
            self.assertEqual(len(result["query_coverage"]["completed_queries"]), 22)


if __name__ == "__main__":
    unittest.main()
