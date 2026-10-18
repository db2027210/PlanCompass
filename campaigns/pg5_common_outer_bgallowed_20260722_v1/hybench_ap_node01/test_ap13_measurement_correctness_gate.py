#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from campaigns.pg5_common_outer_bgallowed_20260722_v1.hybench_ap_node01.baseline_once import (
    evaluate_ap13_measurement_correctness,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    expected = {query_id: digest(f"AP{query_id}") for query_id in range(1, 14)}
    auxiliary = digest("auxiliary")
    rows = []
    for index in range(116):
        query_id = (index % 13) + 1
        rows.append({
            "event": "query_complete",
            "sql_sha256": auxiliary if index == 115 else expected[query_id],
            "row_count": index,
            "result_sha256": digest(f"parameterized-result-{index}"),
            "success": True,
            "commit_after_query": True,
            "error": None,
        })

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "measurement_queries.jsonl"
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        passed = evaluate_ap13_measurement_correctness(path, expected)
        assert passed["status"] == "passed", passed
        assert passed["event_count"] == 116
        assert passed["auxiliary_sql_sha256"] == [auxiliary]
        assert passed["parameterized_result_policy"][
            "cross_execution_result_sha256_equality_required"
        ] is False

        rows[0]["success"] = False
        rows[0]["error"] = "synthetic failure"
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        failed = evaluate_ap13_measurement_correctness(path, expected)
        assert failed["status"] == "failed", failed
        assert failed["failures"]

    print("ap13 measurement correctness gate tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
