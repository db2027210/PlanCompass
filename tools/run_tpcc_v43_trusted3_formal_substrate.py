#!/usr/bin/env python3
"""State-neutral strict-serial substrate for exactly three formal trusted points."""

from __future__ import annotations

import hashlib
from pathlib import Path


SOURCE = Path(
    "/home/anonymous/program/resource_adaptive/"
    "tools/run_tpcc_v36_unique_exact_strict_serial.py"
)
EXPECTED_SOURCE_SHA256 = (
    "eb4f20679e0c8f2dec49520874ba40aebc0cdb7f30ec9939d98d6234692fa3eb"
)


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"TPC-C v43 trusted3 substrate patch mismatch: {label}")
    return source.replace(old, new, 1)


def _transformed_source() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C v43 trusted3 source SHA mismatch")
    replacements = (
        ("MAX_REAL_WORKLOAD_INVOCATIONS = 5", "MAX_REAL_WORKLOAD_INVOCATIONS = 3", "cap"),
        (
            """            if exact_drift_v37 or fresh_unique_v38:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
""",
            """            v43_formal_trusted3 = bool(
                analysis.get(\"v43_formal_trusted3_contract\")
            )
            if (exact_drift_v37 or fresh_unique_v38) and not v43_formal_trusted3:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
""",
            "calibration",
        ),
        (
            """    if len(candidates) != 5:
        raise RuntimeError(f\"expected exactly five candidates, got {len(candidates)}\")
    if [item.get(\"rank\") for item in candidates] != [1, 2, 3, 4, 5]:
""",
            """    if len(candidates) != 3:
        raise RuntimeError(f\"expected exactly three trusted candidates, got {len(candidates)}\")
    if [item.get(\"rank\") for item in candidates] != [1, 2, 3]:
""",
            "candidate count",
        ),
        (
            "if len(set(fingerprint_ids)) != 5:",
            "if len(set(fingerprint_ids)) != 3:",
            "fingerprints",
        ),
        (
            """        len(successful) == MAX_REAL_WORKLOAD_INVOCATIONS
        and [int(item[\"rank\"]) for item in successful] == [1, 2, 3, 4, 5]
""",
            """        len(successful) == MAX_REAL_WORKLOAD_INVOCATIONS
        and [int(item[\"rank\"]) for item in successful] == [1, 2, 3]
""",
            "completion ranks",
        ),
    )
    for old, new, label in replacements:
        source = _replace_once(source, old, new, label)
    return source


def _validate_formal_trusted3(analysis: dict, candidates: list[dict]) -> None:
    contract = analysis.get("v43_formal_trusted3_contract") or {}
    if (
        contract.get("contract") != "tpcc_v43_formal_trusted3_only_v1"
        or contract.get("formal_trusted_ranks") != [1, 2, 3]
        or int(contract.get("probe_formal_call_count", -1)) != 0
        or contract.get("probe_formal_eligible") is not False
        or contract.get("probe_gradient_eligible") is not False
        or contract.get("probe_incumbent_eligible") is not False
        or [item.get("rank") for item in candidates] != [1, 2, 3]
        or any(item.get("selection_role") != "trusted" for item in candidates)
        or any(item.get("diagnostic_only") is not True for item in candidates)
        or len({item["three_level_fingerprint"]["id"] for item in candidates}) != 3
    ):
        raise RuntimeError("TPC-C v43 formal trusted3 substrate contract mismatch")


def load_runtime() -> dict:
    namespace = {
        "__name__": "tpcc_v43_trusted3_formal_substrate_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(compile(_transformed_source(), str(SOURCE), "exec"), namespace)
    original_validate = namespace["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original_validate(analysis_path, replay_root)
        _validate_formal_trusted3(result[0], result[2])
        return result

    namespace["validate_generation_and_top5"] = validate_generation_and_top5
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
