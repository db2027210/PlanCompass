#!/usr/bin/env python3
"""Persist deterministic a6 derived I/O evidence without rewriting raw results."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import formal_pg_engine as engine
import formal_pg_point_runner as points


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_6"
FROZEN = LANE_ROOT / "bundle" / "attempt6_frozen"
IO_CONTRACT_SHA256 = (
    "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    result_path = ROOT / "point_result.json"
    gate_path = ROOT / "baseline_gate_decision.json"
    state_path = ROOT / "point_state.json"
    raw_path = ROOT / "measurement_resource_samples.jsonl"
    query_path = ROOT / "measurement_queries.jsonl"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if result.get("status") != "complete" or gate.get("outer_allowed") is not False:
        raise RuntimeError("a6 terminal fail-closed evidence is not ready")
    failed = [name for name, passed in gate["checks"].items() if passed is not True]
    if failed != ["stability"]:
        raise RuntimeError(f"unexpected a6 gate failures: {failed}")
    contract_path = FROZEN / "io_utilization_contract.json"
    if sha256_file(contract_path) != IO_CONTRACT_SHA256:
        raise RuntimeError("public I/O utilization contract checksum mismatch")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    required = tuple(contract["persist_required_fields"])
    engine.SHARED_CONFIG = FROZEN
    points.SHARED_CONFIG = FROZEN
    cutoff = float(result["measurement"]["measurement_cutoff_epoch"])
    samples = points._stability_samples_from_evidence(
        "job",
        "memory",
        raw_path,
        query_path,
        None,
        cutoff_epoch=cutoff,
    )
    rows = []
    for sample in samples:
        row = {
            "schema_version": 1,
            "start_epoch": sample["start_epoch"],
            "end_epoch": sample["end_epoch"],
            "seconds": sample["seconds"],
            "qps": sample["qps"],
            "cpu_utilization": sample["cpu_utilization"],
            "memory_utilization": sample["memory_utilization"],
            "io_utilization": sample["io_utilization"],
            "read_bps": sample["read_bps"],
            "write_bps": sample["write_bps"],
            "read_cap_bps": sample["read_cap_bps"],
            "write_cap_bps": sample["write_cap_bps"],
            "io_total_cap_bps": sample["io_total_cap_bps"],
            "io_utilization_formula": sample["io_utilization_definition"],
        }
        missing = [field for field in required if field not in row]
        if missing:
            raise RuntimeError(f"derived row misses public I/O fields: {missing}")
        expected = (row["read_bps"] + row["write_bps"]) / row["io_total_cap_bps"]
        if not math.isclose(row["io_utilization"], expected, rel_tol=1e-12, abs_tol=1e-12):
            raise RuntimeError("derived I/O utilization identity mismatch")
        for field in ("cpu_utilization", "memory_utilization", "io_utilization"):
            value = float(row[field])
            if not math.isfinite(value) or value < 0.0:
                raise RuntimeError(f"invalid unbounded raw utilization: {field}={value}")
        rows.append(row)
    derived_path = ROOT / "measurement_derived_resource_samples.jsonl"
    if derived_path.exists() or derived_path.is_symlink():
        raise RuntimeError(f"derived evidence already exists: {derived_path}")
    atomic_jsonl(derived_path, rows)
    maxima = {
        field: max(float(row[field]) for row in rows)
        for field in ("cpu_utilization", "memory_utilization", "io_utilization")
    }
    evidence = {
        "schema_version": 1,
        "status": "complete",
        "source_point_result_path": str(result_path),
        "source_point_result_sha256": sha256_file(result_path),
        "source_raw_counter_path": str(raw_path),
        "source_raw_counter_sha256": sha256_file(raw_path),
        "source_query_path": str(query_path),
        "source_query_sha256": sha256_file(query_path),
        "derived_resource_samples_path": str(derived_path),
        "derived_resource_samples_sha256": sha256_file(derived_path),
        "derived_sample_count": len(rows),
        "io_utilization_contract_path": str(contract_path),
        "io_utilization_contract_sha256": sha256_file(contract_path),
        "io_formula": contract["formula"],
        "persist_required_fields": list(required),
        "required_fields_present_in_every_row": True,
        "io_identity_verified_every_row": True,
        "raw_utilization_validation": "finite_and_non_negative_no_upper_bound",
        "raw_utilization_persistence": "original_value_without_clipping",
        "resource_utilization_maxima": maxima,
        "point_result_rewritten": False,
        "raw_counter_evidence_rewritten": False,
        "baseline_gate_decision_rewritten": False,
        "outer_allowed": False,
        "accepted_shrink_count": 0,
    }
    evidence_path = ROOT / "resource_utilization_evidence.json"
    atomic_json(evidence_path, evidence)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["resource_utilization_evidence"] = {
        "path": str(evidence_path),
        "sha256": sha256_file(evidence_path),
        "derived_samples_path": str(derived_path),
        "derived_samples_sha256": sha256_file(derived_path),
    }
    state["accepted_shrink_count"] = 0
    atomic_json(state_path, state)
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
