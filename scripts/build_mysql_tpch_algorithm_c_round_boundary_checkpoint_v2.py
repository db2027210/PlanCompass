#!/usr/bin/env python3
"""Seal a TPC-H Top-4 C fresh-search round boundary with conservative dedup recovery."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v5 as predecessor


SCENE = "tpch_sf4_memory"
TOP4 = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
    / "T.tpch_memory.operator_physics_v1.json"
)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def tpch_frozen_fresh_search_tables(
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if list(state.get("active_scenes") or []) != [SCENE]:
        raise RuntimeError("TPC-H boundary active scene differs")
    contract_path = Path(str(state.get("timeout_rows_contract_path") or "")).resolve()
    if (
        not contract_path.is_file() or contract_path.is_symlink()
        or predecessor.base.alg.sha(contract_path)
        != str(state.get("timeout_rows_contract_sha256") or "")
    ):
        raise RuntimeError("TPC-H boundary timeout contract differs")
    contract = _mapping(predecessor.base.alg.load(contract_path), "TPC-H contract")
    top4_binding = _mapping(
        _mapping(contract.get("source_bindings"), "TPC-H source bindings").get(
            "top4_table"
        ),
        "TPC-H Top-4 binding",
    )
    if (
        Path(str(top4_binding.get("path") or "")).name != TOP4.name
        or predecessor.base.alg.sha(TOP4) != str(top4_binding.get("sha256") or "")
    ):
        raise RuntimeError("TPC-H Top-4 binding differs")

    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    prior = predecessor.verifier.verify_checkpoint(prior_path)
    reconstructed = _mapping(
        prior.get("reconstructed_state"), "prior reconstructed state"
    )
    active = _mapping(reconstructed.get("active_by_scene"), "prior active state")
    ranges = _mapping(reconstructed.get("ranges_by_scene"), "prior range state")
    tables = {
        scene: {
            "parameter_space": {
                "active_params": copy.deepcopy(list(active[scene])),
                "ranges": copy.deepcopy(_mapping(ranges[scene], f"{scene} ranges")),
            }
        }
        for scene in predecessor.SCENES
    }
    table = _mapping(predecessor.base.alg.load(TOP4), "TPC-H Top-4 table")
    space = _mapping(table.get("parameter_space"), "TPC-H parameter space")
    if (
        list(space.get("active_params") or [])
        != [
            "io_block_read_cost", "memory_block_read_cost",
            "row_evaluate_cost", "disk_temptable_row_cost",
        ]
        or set(_mapping(space.get("ranges"), "TPC-H ranges"))
        != set(space["active_params"])
    ):
        raise RuntimeError("TPC-H Top-4 parameter space differs")
    tables[SCENE] = table
    return tables


_base_build = predecessor.build


def build(run_root: Path) -> dict[str, Any]:
    document = _base_build(run_root)
    document.update({
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": predecessor.base.alg.sha(Path(__file__).resolve()),
        "tpch_top4_fresh_search_boundary": {
            "scene": SCENE,
            "top4_table_path": str(TOP4),
            "top4_table_sha256": predecessor.base.alg.sha(TOP4),
            "xp_top4_release_required": False,
            "completed_rounds_preserved": True,
        },
    })
    document.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = predecessor.base._canonical_sha256(
        copy.deepcopy(document)
    )
    return document


predecessor.frozen_fresh_search_tables = tpch_frozen_fresh_search_tables
predecessor.build = build


def main() -> int:
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
