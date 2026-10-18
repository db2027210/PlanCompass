#!/usr/bin/env python3
"""Self-test for repeated ABC common-domain QPS aggregation."""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import build_pg_algorithm_abc_matched_absolute_qps as builder  # noqa: E402


PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def make_run(
    root: Path,
    repetition: int,
    method_order: Tuple[str, ...] = builder.METHODS,
) -> None:
    contract_scenes = {}
    stage_results = {method: {"scene_results": {}} for method in builder.METHODS}
    for scene in builder.SCENES:
        contract_scenes[scene] = {
            "methods": {method: {"params": PARAMS} for method in builder.METHODS}
        }
        for method_index, method in enumerate(builder.METHODS, start=1):
            paths = []
            for worker_index, worker in enumerate(("w1", "w2"), start=1):
                path = root / "rows" / f"{scene}_{method}_{worker}.json"
                qps = float(10 * method_index * repetition * worker_index)
                write_json(path, {"worker_key": worker, "db_qps": qps})
                paths.append(str(path))
            stage_results[method]["scene_results"][scene] = {"row_paths": paths}
    write_json(root / "authority_manifest.json", {"contract": builder.SOURCE_CONTRACT})
    write_json(root / "state.json", {"stage_results": stage_results})
    write_json(root / "contract.json", {
        "seed": 1000 + repetition,
        "method_order": list(method_order),
        "scenes": contract_scenes,
    })


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        first = root / "rep1"
        second = root / "rep2"
        make_run(first, 1)
        make_run(second, 2, tuple(reversed(builder.METHODS)))
        with mock.patch.object(builder, "verify_common", return_value={"passed": True}):
            report = builder.build([first, second])
        assert report["contract"] == builder.CONTRACT
        assert report["aggregation"] == "balanced_worker_repetition_geometric_mean"
        assert report["algorithm_c_was_terminal_before_a_b_evidence_loaded"] is True
        assert report["distinct_repetition_seeds"] is True
        assert [row["seed"] for row in report["source_runs"]] == [1001, 1002]
        observed = report["scenes"]["job_memory"]["Algorithm A"]
        assert observed["repetitions_per_worker"] == 2
        assert len(observed["raw_qps"]) == 4
        assert math.isclose(observed["qps_geomean"], math.exp(sum(map(math.log, [10, 20, 20, 40])) / 4))

        duplicate_seed = root / "rep3"
        make_run(duplicate_seed, 1)
        with mock.patch.object(builder, "verify_common", return_value={"passed": True}):
            try:
                builder.build([first, duplicate_seed])
            except RuntimeError as exc:
                assert "distinct seeds" in str(exc)
            else:
                raise AssertionError("duplicate validation seeds must be rejected")

    print("PASS: repeated ABC matched absolute-QPS builder", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
