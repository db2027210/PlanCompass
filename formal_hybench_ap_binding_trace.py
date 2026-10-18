#!/usr/bin/env python3
"""Semantic validator and sealer for formal HyBench AP runtime binding traces."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any, Mapping

from formal_mysql_binding_trace import BindingTraceError, finalize_binding_trace, validate_event


_AP_QUERY = re.compile(r"AP-(?:[1-9]|1[0-3])\Z")
_WORKER = re.compile(r"T([1-9][0-9]*)\Z")


def validate_hybench_ap_trace(
    events_path: Path, *, expected_run_id: str, expected_workers: int = 4
) -> list[Mapping[str, Any]]:
    """Validate AP-specific completeness before the generic manifest is published."""
    if expected_workers < 1:
        raise BindingTraceError("expected_workers must be positive")
    if not events_path.is_file() or events_path.is_symlink():
        raise BindingTraceError("HyBench AP trace is absent or non-regular")
    rows: list[Mapping[str, Any]] = []
    for line_number, raw in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(raw)
            validate_event(row, expected_run_id=expected_run_id)
        except Exception as error:
            raise BindingTraceError(f"invalid HyBench AP event line {line_number}: {error}") from error
        if row["benchmark"] != "hybench_ap":
            raise BindingTraceError("non-HyBench-AP event in AP trace")
        if row["attempt"] != 1 or row["retry"] != 0:
            raise BindingTraceError("candidate AP client has no retry loop; attempt/retry must be 1/0")
        if not (_AP_QUERY.fullmatch(row["query_id"]) or row["query_id"] == "AP-2-RAND"):
            raise BindingTraceError(f"invalid AP query identity {row['query_id']!r}")
        match = _WORKER.fullmatch(row["worker_or_terminal"])
        if match is None or not 1 <= int(match.group(1)) <= expected_workers:
            raise BindingTraceError("AP worker identity is outside the frozen worker set")
        state = row["seed_state"]
        if set(state) != {"rng_draw_count_before", "rng_draw_count_at_execute"}:
            raise BindingTraceError("AP seed_state fields differ from candidate contract")
        if not all(isinstance(state[key], int) and state[key] >= 0 for key in state):
            raise BindingTraceError("AP RNG draw counts must be non-negative integers")
        if state["rng_draw_count_at_execute"] < state["rng_draw_count_before"]:
            raise BindingTraceError("AP RNG draw count moved backwards")
        derivation = row["seed_derivation"]
        required_derivation = {
            "base_random_seed", "database_rand_observed", "database_rand_value",
            "derived_worker_seed", "source"
        }
        if set(derivation) != required_derivation:
            raise BindingTraceError("AP seed_derivation fields differ from candidate contract")
        rows.append(row)

    by_worker: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    workers: set[str] = set()
    for row in rows:
        worker = row["worker_or_terminal"]
        workers.add(worker)
        by_worker[worker].append(row)
    required_workers = {f"T{index}" for index in range(1, expected_workers + 1)}
    if workers != required_workers:
        raise BindingTraceError(f"AP worker set differs: expected {sorted(required_workers)}, saw {sorted(workers)}")

    for worker in sorted(workers):
        ordered = sorted(by_worker[worker], key=lambda row: row["sequence"])
        sequences = [row["sequence"] for row in ordered]
        if sequences != list(range(1, len(sequences) + 1)):
            raise BindingTraceError(f"non-contiguous AP sequence for {worker}")
        previous_draw_count = -1
        index = 0
        while index < len(ordered):
            row = ordered[index]
            if row["query_id"] == "AP-2-RAND":
                if index + 1 >= len(ordered) or ordered[index + 1]["query_id"] != "AP-2":
                    raise BindingTraceError("AP-2 RAND selector is not followed by its main SQL")
                selector = row
                main = ordered[index + 1]
                selector_derivation = selector["seed_derivation"]
                main_derivation = main["seed_derivation"]
                if selector_derivation["database_rand_observed"] is not True:
                    raise BindingTraceError("AP-2 selector lacks its observed database RAND value")
                if main["seed_derivation"]["database_rand_observed"] is not True:
                    raise BindingTraceError("AP-2 main query lacks database RAND provenance")
                bindings = main["actual_bindings"]
                if not bindings or bindings[0]["position"] != 1 or not isinstance(bindings[0]["value"], int):
                    raise BindingTraceError("AP-2 main query lacks the integer database RAND binding")
                selector_value = selector_derivation["database_rand_value"]
                main_value = main_derivation["database_rand_value"]
                if not isinstance(selector_value, int) or selector_value != main_value:
                    raise BindingTraceError("AP-2 selector and main database RAND values differ")
                if main_value != bindings[0]["value"]:
                    raise BindingTraceError("AP-2 database RAND value differs from main binding")
                if selector["seed_state"]["rng_draw_count_before"] != main["seed_state"]["rng_draw_count_before"]:
                    raise BindingTraceError("AP-2 SQL events disagree on initial RNG state")
                index += 2
            elif row["query_id"] == "AP-2":
                raise BindingTraceError("AP-2 main SQL lacks its immediately preceding RAND selector")
            else:
                if row["seed_derivation"]["database_rand_observed"] is not False \
                        or row["seed_derivation"]["database_rand_value"] is not None:
                    raise BindingTraceError("non-AP-2 SQL claims database RAND provenance")
                index += 1
            before = row["seed_state"]["rng_draw_count_before"]
            if before < previous_draw_count:
                raise BindingTraceError(f"AP RNG state regressed for {worker}")
            previous_draw_count = before
    return rows


def validate_and_finalize(
    events_path: Path, *, expected_run_id: str, expected_workers: int = 4
) -> Mapping[str, Any]:
    validate_hybench_ap_trace(
        events_path, expected_run_id=expected_run_id, expected_workers=expected_workers
    )
    return finalize_binding_trace(events_path, expected_run_id=expected_run_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("events", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-workers", type=int, default=4)
    args = parser.parse_args()
    manifest = validate_and_finalize(
        args.events, expected_run_id=args.run_id, expected_workers=args.expected_workers
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
