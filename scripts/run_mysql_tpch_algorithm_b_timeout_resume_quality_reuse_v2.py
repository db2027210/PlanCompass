#!/usr/bin/env python3
"""Resume TPC-H B while reusing its deterministic failed-soft quality gate."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_tpch_algorithm_b_timeout_resume_v1 as predecessor


SOURCE_QUALITY = (
    predecessor.SOURCE
    / "fresh_calibration/tpch_sf4_memory/fresh_model_quality_gate.json"
)
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_quality_reuse_20260830_v3"
    / "algorithm_b_quality_reuse_contract.json"
)


def install_runtime() -> Any:
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_state_document = impl["state_document"]

    contract = alg.load(CONTRACT)
    if (
        contract.get("schema_version") != "mysql_tpch_algorithm_b_quality_reuse_v3"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("quality_gate_replay_required") is not False
        or contract.get("calibration_measurement_replay_required") is not False
        or contract.get("quality_gate_source_path") != str(SOURCE_QUALITY.relative_to(ROOT))
        or contract.get("quality_gate_source_sha256") != alg.sha(SOURCE_QUALITY)
    ):
        raise RuntimeError("TPC-H B quality-reuse contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H B quality-reuse binding differs: {path}")

    def reuse_quality_gate(
        model: Any, validator: Any, sql_set: Any,
        base_params: Mapping[str, float], ranges: Mapping[str, Any],
        candidates: Any, *, scenario_dir: Path, thresholds: Any = None,
        design_override: Any = None,
    ) -> dict[str, Any]:
        del base_params, ranges, candidates, scenario_dir, thresholds
        source = alg.load(SOURCE_QUALITY)
        sidecar = SOURCE_QUALITY.with_name(SOURCE_QUALITY.name + ".sha256")
        if (
            SOURCE_QUALITY.is_symlink() or not SOURCE_QUALITY.is_file()
            or sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii").split()[0] != alg.sha(SOURCE_QUALITY)
            or source.get("passed") is not False
            or source.get("preflight_soft_start") is not True
            or source.get("calibration_design") != design_override
            or int(source.get("calibration_replay_attempt_count") or 0) != 15
        ):
            raise RuntimeError("TPC-H B reusable quality gate differs")
        for design_row in design_override:
            raw = validator(sql_set, dict(design_row["params"]))
            payload = impl["measurement_from_payload"](
                raw.get("resources") or raw.get("resource") or {}, raw,
            )
            model.add(design_row["params"], payload.as_resource(), raw)
        result = copy.deepcopy(source)
        result.update({
            "quality_gate_recomputed": False,
            "quality_gate_reused_after_bit_identical_calibration_restore": True,
            "quality_gate_source_path": str(SOURCE_QUALITY),
            "quality_gate_source_sha256": alg.sha(SOURCE_QUALITY),
            "calibration_measurements_replayed": False,
            "calibration_payloads_reinserted_into_reconstructed_model": True,
        })
        return result

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "quality_reuse_contract_path": str(CONTRACT),
            "quality_reuse_contract_sha256": alg.sha(CONTRACT),
            "quality_gate_recomputed": False,
            "quality_gate_source_path": str(SOURCE_QUALITY),
            "quality_gate_source_sha256": alg.sha(SOURCE_QUALITY),
        })
        return value

    impl["run_mysql_fresh_model_quality_gate"] = reuse_quality_gate
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
