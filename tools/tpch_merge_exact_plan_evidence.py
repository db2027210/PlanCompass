#!/usr/bin/env python3
"""Merge validated TPC-H plan-regime evidence without running a workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


def load_json_value(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value


def load_json(path: Path) -> Mapping[str, Any]:
    value = load_json_value(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> List[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}:{line_number} must contain an object")
            records.append(value)
    return records


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parameter_key(candidate: Mapping[str, Any]) -> str:
    raw = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("candidate is missing candidate_params")
    normalized = {str(name): float(value) for name, value in raw.items()}
    return canonical_sha256(normalized)


def candidate_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    rows = payload.get("ranked_candidates") or payload.get("top5")
    if not isinstance(rows, list):
        raise ValueError("analysis is missing ranked_candidates/top5")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("analysis candidate must be an object")
        yield row


def result_rows(payload: Any) -> Iterable[Mapping[str, Any]]:
    rows: Any = payload
    if isinstance(payload, Mapping):
        rows = (
            payload.get("successful")
            or payload.get("results")
            or payload.get("merged_results")
            or payload.get("top5")
        )
    if not isinstance(rows, list):
        raise ValueError("result JSON does not contain a result list")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("result row must be an object")
        yield row


def is_successful_replay(row: Mapping[str, Any]) -> bool:
    if row.get("technical_failure") is True:
        return False
    event = str(row.get("event") or "")
    if event.endswith("_technical_failure"):
        return False
    if row.get("actual_resource") is None or row.get("actual_score") is None:
        return False
    if event == "tpch_gradient_real_replay_complete":
        protocol = row.get("measurement_protocol") or {}
        return (
            protocol.get("correctness_passed") is True
            and protocol.get("stable") is True
        )
    if "real_replay_complete" in event:
        return row.get("valid_replay") is True
    return True


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, action="append", required=True)
    parser.add_argument("--events", type=Path, action="append", default=[])
    parser.add_argument("--result-json", type=Path, action="append", default=[])
    parser.add_argument("--output-analysis", type=Path, required=True)
    parser.add_argument("--output-events", type=Path, required=True)
    args = parser.parse_args()

    candidates: Dict[str, Dict[str, Any]] = {}
    candidate_sources: Dict[str, str] = {}
    for path in args.analysis:
        payload = load_json(path)
        for row in candidate_rows(payload):
            key = parameter_key(row)
            candidate = dict(row)
            existing = candidates.get(key)
            if existing is not None:
                existing_plan = (
                    (existing.get("three_level_fingerprint") or {}).get("plan_level")
                )
                candidate_plan = (
                    (candidate.get("three_level_fingerprint") or {}).get("plan_level")
                )
                if existing_plan != candidate_plan:
                    raise ValueError(
                        f"parameter vector {key} has inconsistent plan fingerprints"
                    )
                if existing.get("final_estimation_metadata") is None and candidate.get(
                    "final_estimation_metadata"
                ) is not None:
                    candidates[key] = candidate
                    candidate_sources[key] = str(path)
                continue
            candidates[key] = candidate
            candidate_sources[key] = str(path)

    successful: Dict[str, Dict[str, Any]] = {}
    event_sources: Dict[str, str] = {}
    for path in args.events:
        rows = load_jsonl(path)
        for row in rows:
            if not is_successful_replay(row):
                continue
            key = parameter_key(row)
            successful[key] = dict(row)
            event_sources[key] = str(path)
    for path in args.result_json:
        payload = load_json_value(path)
        for row in result_rows(payload):
            if not is_successful_replay(row):
                continue
            normalized = dict(row)
            normalized["event"] = "tpch_formal_real_replay_complete"
            normalized["technical_failure"] = False
            normalized["valid_replay"] = True
            key = parameter_key(normalized)
            successful[key] = normalized
            event_sources[key] = str(path)

    missing = sorted(set(successful) - set(candidates))
    if missing:
        raise ValueError(
            "successful evidence is missing source EXPLAIN candidates: "
            + ", ".join(missing)
        )
    if not successful:
        raise ValueError("no successful replay evidence was found")

    analysis_payload = {
        "schema_version": "tpch_merged_exact_plan_evidence_analysis_v1",
        "offline_only": True,
        "new_workload_calls": 0,
        "source_analysis_sha256": {
            str(path): file_sha256(path) for path in args.analysis
        },
        "candidate_source_by_parameter_key": candidate_sources,
        "ranked_candidates": list(candidates.values()),
    }
    event_lines = []
    for key in sorted(successful):
        row = dict(successful[key])
        row["evidence_source_path"] = event_sources[key]
        row["evidence_parameter_key"] = key
        event_lines.append(json.dumps(row, sort_keys=True))

    atomic_text(
        args.output_analysis,
        json.dumps(analysis_payload, indent=2, sort_keys=True) + "\n",
    )
    atomic_text(args.output_events, "\n".join(event_lines) + "\n")
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "successful_evidence_count": len(successful),
                "output_analysis": str(args.output_analysis),
                "output_analysis_sha256": file_sha256(args.output_analysis),
                "output_events": str(args.output_events),
                "output_events_sha256": file_sha256(args.output_events),
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
