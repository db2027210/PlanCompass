from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from formal_hybench_ap_binding_trace import validate_hybench_ap_trace
from formal_mysql_binding_trace import BindingTraceError, BindingTraceWriter


class HyBenchApBindingTraceTamperTest(unittest.TestCase):
    def _valid_trace(self, root: Path) -> Path:
        path = root / "bindings.jsonl"
        writer = BindingTraceWriter(path, "tamper-test")
        common = {
            "benchmark": "hybench_ap",
            "worker_or_terminal": "T1",
            "attempt": 1,
            "seed_state": {"rng_draw_count_before": 8, "rng_draw_count_at_execute": 8},
            "outcome": "success",
        }
        writer.record(
            **common,
            query_id="AP-2-RAND",
            sequence=1,
            seed_derivation={
                "base_random_seed": 4508333,
                "database_rand_observed": True,
                "database_rand_value": 271828,
                "derived_worker_seed": 991,
                "source": "Client.deriveSeed(worker,ordinal)",
            },
            actual_bindings=[{"position": 1, "jdbc_type": "INT", "value": 600000}],
            executable_sql=(
                "SELECT applicantid FROM loanapps WHERE applicantid < 600000 "
                "ORDER BY RAND() LIMIT 1"
            ),
            template_sql=(
                "SELECT applicantid FROM loanapps WHERE applicantid < ? "
                "ORDER BY RAND() LIMIT 1"
            ),
        )
        writer.record(
            **common,
            query_id="AP-2",
            sequence=2,
            seed_derivation={
                "base_random_seed": 4508333,
                "database_rand_observed": True,
                "database_rand_value": 271828,
                "derived_worker_seed": 991,
                "source": "Client.deriveSeed(worker,ordinal)",
            },
            actual_bindings=[{"position": 1, "jdbc_type": "INT", "value": 271828}],
            executable_sql="SELECT * FROM customer WHERE custid = 271828",
            template_sql="SELECT * FROM customer WHERE custid = ?",
        )
        return path

    def test_equal_database_rand_evidence_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._valid_trace(Path(raw))
            rows = validate_hybench_ap_trace(
                path, expected_run_id="tamper-test", expected_workers=1
            )
            self.assertEqual(2, len(rows))

    def test_tampered_selector_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._valid_trace(Path(raw))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rows[0]["seed_derivation"]["database_rand_value"] = 7
            path.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
            )
            with self.assertRaisesRegex(BindingTraceError, "selector and main.*differ"):
                validate_hybench_ap_trace(
                    path, expected_run_id="tamper-test", expected_workers=1
                )

    def test_tampered_main_binding_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = self._valid_trace(Path(raw))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rows[1]["actual_bindings"][0]["value"] = 7
            path.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
            )
            with self.assertRaisesRegex(BindingTraceError, "differs from main binding"):
                validate_hybench_ap_trace(
                    path, expected_run_id="tamper-test", expected_workers=1
                )


if __name__ == "__main__":
    unittest.main()
