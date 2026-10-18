#!/usr/bin/env python3
"""Resume TPC-H B from its frozen partial Outer-2 ranking boundary."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_tpch_algorithm_b_timeout_resume_quality_reuse_v2 as predecessor


RANKINGS = (
    predecessor.predecessor.SOURCE / "outer_002/predicted_rankings.json"
)
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_b_outer2_ranking_resume_20260830_v4"
    / "algorithm_b_outer2_ranking_resume_contract.json"
)
SCENE = "tpch_sf4_memory"


def install_runtime() -> Any:
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_rank_scene = impl["rank_scene"]
    original_state_document = impl["state_document"]
    contract = alg.load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_tpch_algorithm_b_outer2_ranking_resume_v4"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("frozen_partial_outer") != 2
        or contract.get("frozen_ranking_recomputed") is not False
        or contract.get("frozen_ranking_path") != str(RANKINGS.relative_to(ROOT))
        or contract.get("frozen_ranking_sha256") != alg.sha(RANKINGS)
    ):
        raise RuntimeError("TPC-H B frozen Outer-2 ranking contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H B frozen ranking binding differs: {path}")

    def rank_scene(**kwargs: Any) -> dict[str, Any]:
        if kwargs.get("scene") == SCENE and int(kwargs.get("round_index") or 0) == 2:
            sidecar = RANKINGS.with_name(RANKINGS.name + ".sha256")
            document = alg.load(RANKINGS)
            scene_audit = (document.get("scene_audit") or {}).get(SCENE)
            selected = (document.get("selected_top_k") or {}).get(SCENE)
            if (
                RANKINGS.is_symlink() or not RANKINGS.is_file()
                or sidecar.is_symlink() or not sidecar.is_file()
                or sidecar.read_text(encoding="ascii").split()[0] != alg.sha(RANKINGS)
                or document.get("outer_attempt") != 2
                or not isinstance(scene_audit, dict)
                or scene_audit.get("selected_top5") != selected
                or len(selected or []) != 5
            ):
                raise RuntimeError("TPC-H B frozen Outer-2 ranking evidence differs")
            result = copy.deepcopy(scene_audit)
            result.update({
                "partial_outer_ranking_recomputed": False,
                "partial_outer_ranking_source_path": str(RANKINGS),
                "partial_outer_ranking_source_sha256": alg.sha(RANKINGS),
            })
            return result
        return original_rank_scene(**kwargs)

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "outer2_ranking_resume_contract_path": str(CONTRACT),
            "outer2_ranking_resume_contract_sha256": alg.sha(CONTRACT),
            "partial_outer_ranking_recomputed": False,
            "partial_outer_ranking_source_path": str(RANKINGS),
            "partial_outer_ranking_source_sha256": alg.sha(RANKINGS),
        })
        return value

    impl["rank_scene"] = rank_scene
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
