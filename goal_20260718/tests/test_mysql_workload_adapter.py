from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from mysql_workload_adapter import (
    MySQLEndpoint, build_job_manifest, generate_benchbase_xml,
    generate_hybench_properties, parse_benchbase_summary, parse_hybench_stdout,
    parse_hybench_mode_metrics,
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
            self.assertEqual(root.findtext("works/work/rate"), "unlimited")
            self.assertEqual(root.findtext("afterload"), "/benchmarks/tpch/postload-mysql.sql")
            self.assertIsNone(root.findtext("works/work/serial"))
            self.assertEqual(root.findtext("works/work/weights"),
                             ",".join(str(value) for value in weights))
            all_queries = generate_benchbase_xml("tpch", ENDPOINT, Path(directory) / "all",
                                                 duration_s=12, terminals=3)
            self.assertEqual(ET.parse(all_queries).getroot().findtext("works/work/serial"), "true")
            capped = generate_benchbase_xml(
                "tpcc", ENDPOINT, Path(directory) / "capped",
                duration_s=12, terminals=3, rate_limit=145.0,
            )
            self.assertEqual(ET.parse(capped).getroot().findtext("works/work/rate"), "145")
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
        self.assertEqual(xp.qps, 8.0)
        ap_histogram = parse_hybench_mode_metrics(
            "AP Query 1 : avg rt : 100.0 | 95% rt : 120 ms | 99% rt : 140 ms\n"
            "AP Query 2 : avg rt : 300.0 | 95% rt : 330 ms | 99% rt : 350 ms", "ap"
        )
        self.assertEqual(ap_histogram.qps, 5.0)

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
                             concurrency=2, repetitions=2, runner=fake)
            self.assertGreater(result["metrics"]["qps"], 0)
            self.assertEqual(len(result["metrics"]["explain_samples"]), 1)
            self.assertEqual(len(calls), 3)
            self.assertNotIn("secret", " ".join(calls[0][0]))
            sql.write_text("DELETE FROM t", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_job(manifest, ENDPOINT, root / "run2")

    def test_job_rejects_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.sql"; path.write_text("UPDATE t SET x=1", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_job_manifest([path], Path(directory) / "out")
            path.write_text("SELECT 1; DROP TABLE t", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_job_manifest([path], Path(directory) / "out2")


if __name__ == "__main__":
    unittest.main()
