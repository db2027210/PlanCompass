#!/usr/bin/env python3
"""Load the sealed no-retry runner with the v39 calibration metadata gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


SOURCE = Path("/home/anonymous/program/resource_adaptive/tools/run_tpcc_v36_unique_exact_strict_serial.py")
EXPECTED_SOURCE_SHA256 = "eb4f20679e0c8f2dec49520874ba40aebc0cdb7f30ec9939d98d6234692fa3eb"
OLD = """            if exact_drift_v37 or fresh_unique_v38:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
"""
NEW = """            v39_absorption = (
                (analysis.get(\"v39_plan_regime_absorption_contract\") or {}).get(\"contract\")
                == \"tpcc_v39_clean_probe_plan_regime_absorption_v1\"
            )
            if (exact_drift_v37 or fresh_unique_v38) and not v39_absorption:
                calibration = candidate.get(\"plan_regime_calibration\") or {}
"""


def _validate_v39_contract(analysis: dict, candidates: list[dict]) -> None:
    contract = analysis.get("v39_plan_regime_absorption_contract") or {}
    certificate = contract.get("promotion_certificate") or {}
    promoted_key = str(contract.get("absorbed_probe_exact_point_key") or "")
    required = {
        "feature_ratio", "predicted_resource", "utilization", "dScore/dU",
        "T", "confidence", "g_j", "eta", "delta_param", "candidate_score",
    }
    historical: set[str] = set()
    for descriptor in analysis.get("source_batches") or []:
        path = Path(str(descriptor["selection_path"])).resolve()
        if not path.is_file() or _sha256(path) != descriptor.get("selection_sha256"):
            raise RuntimeError("TPC-C v39 historical selection SHA mismatch")
        source = json.loads(path.read_text(encoding="utf-8"))
        historical.update(
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in source.get("top5") or []
        )
    ids = [str((item.get("three_level_fingerprint") or {}).get("id") or "") for item in candidates]
    promoted = [item for item in candidates if str(item.get("exact_point_key")) == promoted_key]
    source_rows = certificate.get("source_rows") or []
    if (
        contract.get("contract") != "tpcc_v39_clean_probe_plan_regime_absorption_v1"
        or contract.get("five_new_noncorner_fingerprints") is not True
        or contract.get("same_rank_retry_allowed") is not False
        or contract.get("technical_call_advances_to_next_unique_rank") is not True
        or contract.get("state_neutral") is not True
        or int(contract.get("physical_workload_calls_added", -1)) != 0
        or contract.get("historical_probe_roles_rewritten") is not False
        or int(contract.get("promotion_evaluation_rows", -1)) != 3
        or int(contract.get("formal_state_update_rows", -1)) != 0
        or certificate.get("contract")
        != "tpcc_v39_accumulated_clean_probe_promotion_certificate_v1"
        or certificate.get("passed") is not True
        or int(certificate.get("promotion_evaluation_rows", -1)) != 3
        or int(certificate.get("formal_state_update_rows", -1)) != 0
        or len(source_rows) != 3
        or len({row.get("selection_sha256") for row in source_rows}) != 3
        or len({row.get("ledger_sha256") for row in source_rows}) != 3
        or any(
            row.get("selection_role") != "uncertain_probe"
            or row.get("trusted_training_eligible") is not False
            for row in source_rows
        )
        or [item.get("selection_role") for item in candidates]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(ids) != 5
        or "" in ids
        or len(set(ids)) != 5
        or bool(set(ids) & historical)
        or any(item.get("boundary_corner") is not False for item in candidates)
        or any(required - set(item) for item in candidates)
        or len(promoted) != 1
        or int(promoted[0].get("rank", -1)) > 3
        or promoted[0].get("trusted") is not True
        or (promoted[0].get("accumulated_probe_promotion_certificate") or {}).get("passed")
        is not True
    ):
        raise RuntimeError("TPC-C v39 absorption/promotion runner contract mismatch")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_runtime() -> dict:
    source = SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C v39 sealed runner source SHA mismatch")
    if source.count(OLD) != 1:
        raise RuntimeError("TPC-C v39 runner patch anchor mismatch")
    transformed = source.replace(OLD, NEW, 1)
    namespace = {
        "__name__": "tpcc_v39_sealed_no_retry_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(compile(transformed, str(SOURCE), "exec"), namespace)
    original_validate = namespace["validate_generation_and_top5"]

    def validate_generation_and_top5(analysis_path: Path, replay_root: Path):
        result = original_validate(analysis_path, replay_root)
        _validate_v39_contract(result[0], result[2])
        return result

    namespace["validate_generation_and_top5"] = validate_generation_and_top5
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
