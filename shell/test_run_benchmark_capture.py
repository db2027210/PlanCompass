#!/usr/bin/env python3
"""Focused tests for complete AP-query accounting in the benchmark wrapper."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET


MODULE_PATH = Path(__file__).with_name("run_benchmark_capture.py")
SPEC = importlib.util.spec_from_file_location("run_benchmark_capture_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BenchbaseQueryCoverageTest(unittest.TestCase):
    def test_tpch_full_config_is_one_terminal_serial_q1_to_q22(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tpch.xml"
            source.write_text(
                "<parameters><url>x</url><username>x</username><password>x</password>"
                "<scalefactor>1</scalefactor><terminals>2</terminals><works><work>"
                "<serial>false</serial><time>300</time><weights>"
                + ",".join("1" for _ in MODULE.TPCH_QUERY_NAMES)
                + "</weights></work></works><transactiontypes>"
                + "".join(
                    f"<transactiontype><name>{name}</name><id>{index}</id></transactiontype>"
                    for index, name in enumerate(MODULE.TPCH_QUERY_NAMES, start=1)
                )
                + "</transactiontypes></parameters>\n",
                encoding="utf-8",
            )
            destination = root / "full.xml"
            MODULE.write_tpch_full_serial_config(
                source,
                destination,
                endpoint=("127.0.0.1", "5432", "tpch", "postgres", "secret"),
            )
            generated = ET.parse(destination).getroot()

        self.assertEqual(generated.findtext("terminals"), "1")
        self.assertEqual(generated.findtext("./works/work/serial"), "true")
        self.assertIsNone(generated.find("./works/work/time"))
        self.assertEqual(
            [
                item.findtext("name")
                for item in generated.findall("./transactiontypes/transactiontype")
            ],
            list(MODULE.TPCH_QUERY_NAMES),
        )

    def test_tpch_parallel_lpt_partition_is_complete_and_unique(self) -> None:
        lanes = MODULE.partition_tpch_queries(5)
        flattened = [query_name for lane in lanes for query_name in lane]

        self.assertEqual(len(lanes), 5)
        self.assertEqual(len(flattened), 22)
        self.assertEqual(len(set(flattened)), 22)
        self.assertEqual(set(flattened), set(MODULE.TPCH_QUERY_NAMES))
        estimated_loads = [
            sum(MODULE.TPCH_HISTORICAL_RUNTIME_HINT_SECONDS[name] for name in lane)
            for lane in lanes
        ]
        self.assertLess(max(estimated_loads) - min(estimated_loads), 20.0)

    def test_tpch_subset_lane_renumbers_transaction_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tpch.xml"
            source.write_text(
                "<parameters><terminals>1</terminals><works><work><serial>true</serial>"
                "<weights>1,1,1</weights></work></works><transactiontypes>"
                "<transactiontype><name>Q1</name><id>1</id></transactiontype>"
                "<transactiontype><name>Q4</name><id>4</id></transactiontype>"
                "<transactiontype><name>Q18</name><id>18</id></transactiontype>"
                "</transactiontypes></parameters>",
                encoding="utf-8",
            )
            destination = root / "lane.xml"
            MODULE.write_tpch_serial_lane_config(
                source,
                destination,
                ("Q18", "Q4"),
            )
            generated = ET.parse(destination).getroot()

        self.assertEqual(
            [
                (item.findtext("name"), item.findtext("id"))
                for item in generated.findall("./transactiontypes/transactiontype")
            ],
            [("Q18", "1"), ("Q4", "2")],
        )

    def test_job_heavy_queries_are_classified_for_dynamic_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queries = []
            for index in range(10):
                path = root / f"{index + 1}a.sql"
                relations = ", ".join(
                    f"table_{number} t{number}"
                    for number in range(index + 1)
                )
                path.write_text(
                    f"SELECT 1 FROM {relations} WHERE true;\n",
                    encoding="utf-8",
                )
                queries.append(path)

            schedule, long_names = MODULE.stagger_job_queries(
                queries,
                workers=8,
                long_query_fraction=0.2,
                max_concurrent_long_queries=2,
            )

        self.assertEqual(set(schedule), set(queries))
        self.assertGreaterEqual(len(long_names), 2)
        self.assertIn("7a.sql", long_names)
        first_short_index = MODULE.next_eligible_job_task_index(
            [(0, index, path) for index, path in enumerate(schedule, start=1)],
            long_query_names=long_names,
            active_long_queries=2,
            max_concurrent_long_queries=2,
        )
        self.assertIsNotNone(first_short_index)
        assert first_short_index is not None
        self.assertNotIn(schedule[first_short_index].name, long_names)

    def test_job_capped_schedule_includes_historical_long_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queries = []
            for name in ("1a.sql", "17b.sql", "25c.sql", "30a.sql"):
                path = root / name
                path.write_text("SELECT 1 FROM title WHERE true;\n", encoding="utf-8")
                queries.append(path)

            schedule, long_names = MODULE.stagger_job_queries(
                queries,
                workers=5,
                long_query_fraction=0.2,
                max_concurrent_long_queries=2,
            )

        self.assertEqual(
            [path.name for path in schedule],
            ["25c.sql", "17b.sql", "30a.sql", "1a.sql"],
        )
        self.assertTrue({"25c.sql", "17b.sql", "30a.sql"}.issubset(long_names))

    def test_job_dispatch_skips_long_task_when_cap_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            long_path = root / "25c.sql"
            another_long_path = root / "17b.sql"
            short_path = root / "1a.sql"
            for path in (long_path, another_long_path, short_path):
                path.write_text("SELECT 1;\n", encoding="utf-8")
            tasks = [(0, 1, another_long_path), (0, 2, short_path)]

            index = MODULE.next_eligible_job_task_index(
                tasks,
                long_query_names={long_path.name, another_long_path.name},
                active_long_queries=2,
                max_concurrent_long_queries=2,
            )

        self.assertEqual(index, 1)

    def test_job_dispatch_skips_active_long_query_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            same_family = root / "17c.sql"
            other_family = root / "25c.sql"
            short_path = root / "1a.sql"
            for path in (same_family, other_family, short_path):
                path.write_text("SELECT 1;\n", encoding="utf-8")
            tasks = [
                (0, 1, same_family),
                (0, 2, other_family),
                (0, 3, short_path),
            ]

            index = MODULE.next_eligible_job_task_index(
                tasks,
                long_query_names={same_family.name, other_family.name},
                active_long_queries=1,
                max_concurrent_long_queries=6,
                active_long_query_families={"job17_bcd": 1},
                max_concurrent_long_queries_per_family=1,
            )

        self.assertEqual(index, 1)

    def test_job_dispatch_allows_second_long_query_in_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            same_family = root / "17c.sql"
            other_family = root / "25c.sql"
            for path in (same_family, other_family):
                path.write_text("SELECT 1;\n", encoding="utf-8")
            tasks = [(0, 1, same_family), (0, 2, other_family)]

            index = MODULE.next_eligible_job_task_index(
                tasks,
                long_query_names={same_family.name, other_family.name},
                active_long_queries=4,
                max_concurrent_long_queries=6,
                active_long_query_families={"17": 1},
                max_concurrent_long_queries_per_family=2,
            )

        self.assertEqual(index, 0)

    def test_job_eight_client_schedule_preserves_exactly_once_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queries = []
            for index in range(113):
                path = root / f"{index + 1}a.sql"
                path.write_text(
                    f"SELECT {index} FROM table_{index} WHERE true;\n",
                    encoding="utf-8",
                )
                queries.append(path)

            schedule, _long_names = MODULE.stagger_job_queries(
                queries,
                workers=8,
                long_query_fraction=0.2,
                max_concurrent_long_queries=8,
            )

        self.assertEqual(len(schedule), 113)
        self.assertEqual(len(set(schedule)), 113)
        self.assertEqual(set(schedule), set(queries))

    def test_job_eight_client_schedule_prioritizes_historical_long_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queries = []
            for name in ("1a.sql", "17b.sql", "25c.sql", "30a.sql"):
                path = root / name
                path.write_text("SELECT 1 FROM title WHERE true;\n", encoding="utf-8")
                queries.append(path)

            schedule, long_names = MODULE.stagger_job_queries(
                queries,
                workers=8,
                long_query_fraction=0.2,
                max_concurrent_long_queries=8,
            )

        self.assertEqual(
            [path.name for path in schedule],
            ["25c.sql", "17b.sql", "30a.sql", "1a.sql"],
        )
        self.assertTrue({"25c.sql", "17b.sql", "30a.sql"}.issubset(long_names))

    def test_reads_all_tpch_query_counts_from_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root / "tpch_2026-08-06_12-00-00.summary.json"
            summary.write_text("{}\n", encoding="utf-8")
            raw = root / "tpch_2026-08-06_12-00-00.raw.csv"
            with raw.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=("Transaction Name", "Latency (microseconds)"),
                )
                writer.writeheader()
                for query_name in MODULE.TPCH_QUERY_NAMES:
                    writer.writerow({
                        "Transaction Name": query_name,
                        "Latency (microseconds)": "1",
                    })
                writer.writerow({
                    "Transaction Name": "Q1",
                    "Latency (microseconds)": "1",
                })

            counts = MODULE.benchbase_query_counts(summary)

        self.assertEqual(set(counts), set(MODULE.TPCH_QUERY_NAMES))
        self.assertEqual(counts["Q1"], 2)
        self.assertTrue(all(counts[name] >= 1 for name in MODULE.TPCH_QUERY_NAMES))
        self.assertEqual(MODULE.missing_tpch_queries(counts), [])

        del counts["Q22"]
        self.assertEqual(MODULE.missing_tpch_queries(counts), ["Q22"])

    def test_missing_raw_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "tpch_test.summary.json"
            summary.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "raw query evidence"):
                MODULE.benchbase_query_counts(summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
