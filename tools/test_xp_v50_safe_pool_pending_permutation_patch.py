#!/usr/bin/env python3
"""Focused live-lineage tests for XP v50."""

from __future__ import annotations

import json
from pathlib import Path

from tools import xp_v39_exact_point_pending_trust_patch as pending
from tools import xp_v47_safe_pool_pending_lineage_patch as v47
from tools import xp_v50_safe_pool_pending_permutation_patch as v50


ROOT = Path(__file__).resolve().parents[1]
SELECTION = (
    ROOT
    / "history/xp_final_algorithm_fresh10_v38_20260803/batches/cont011/"
    "exact_plan_repeat/selection.json"
)


def _selection():
    return json.loads(SELECTION.read_text(encoding="utf-8"))


def test_live_repeat_is_a_valid_nonidentity_permutation() -> None:
    source = _selection()
    original_roles = [item["selection_role"] for item in source["top5"]]
    original_ids = [item["three_level_fingerprint"]["id"] for item in source["top5"]]
    v50.install(v47, pending)
    sealed = v47.seal_safe_pool_pending_selection(pending, source)
    lineage = sealed["v47_safe_pool_pending_lineage"]
    assert lineage["checks"]["repeat_binding_is_exact_permutation"] is True
    assert lineage["v50_permutation_binding"]["candidate_order_changed"] is False
    assert [item["selection_role"] for item in sealed["top5"]] == original_roles
    assert [item["three_level_fingerprint"]["id"] for item in sealed["top5"]] == original_ids
    source_ids = lineage["source_fingerprint_ids"]
    bound_ids = [
        item["v39_exact_point_binding"]["source_fingerprint_id"]
        for item in sealed["top5"]
    ]
    assert source_ids != bound_ids
    assert set(source_ids) == set(bound_ids)


def test_duplicate_source_binding_fails_closed() -> None:
    source = _selection()
    first_source_id = source["top5"][0]["exact_plan_regime_calibration"][
        "source_fingerprint_id"
    ]
    source["top5"][1]["exact_plan_regime_calibration"][
        "source_fingerprint_id"
    ] = first_source_id
    v50.install(v47, pending)
    try:
        v47.seal_safe_pool_pending_selection(pending, source)
    except RuntimeError:
        pass
    else:
        raise AssertionError("duplicate source binding was not rejected")
