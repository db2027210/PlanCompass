#!/usr/bin/env python3
"""Focused tests for the XP v49 metadata bridge."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

from tools import xp_v49_fresh_selection_metadata_patch as v49


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "history/xp_final_algorithm_fresh10_v38_20260803"
SELECTION = CAMPAIGN / "batches/cont010/selection.json"
REPLAY = CAMPAIGN / "batches/cont010/replay_summary.json"
ADAPTER_PATH = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v33_plan_quarantine/xp_unattended_adapters.py"
)
BUNDLE_ROOT = ADAPTER_PATH.parent


def _adapter():
    if str(BUNDLE_ROOT) not in sys.path:
        sys.path.insert(0, str(BUNDLE_ROOT))
    specification = importlib.util.spec_from_file_location(
        "xp_v49_test_adapter", ADAPTER_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _inputs():
    return (
        json.loads(SELECTION.read_text(encoding="utf-8")),
        json.loads(REPLAY.read_text(encoding="utf-8")),
    )


def test_exact_live_v48_clean5_is_bridged_without_candidate_change() -> None:
    adapter = _adapter()
    selection, replay = _inputs()
    immutable_fields = (
        "candidate_params",
        "candidate_score",
        "predicted_score",
        "predicted_resource",
        "selection_role",
        "three_level_fingerprint",
    )
    before = [
        {name: copy.deepcopy(item.get(name)) for name in immutable_fields}
        for item in selection["top5"]
    ]
    v49.install_adapter(adapter)
    normalized, audit = adapter._normalize_fresh_score_metadata(selection, replay)
    after = [
        {name: copy.deepcopy(item.get(name)) for name in immutable_fields}
        for item in normalized["top5"]
    ]
    assert after == before
    assert normalized["score_regime"] == v49.FRESH_SCORE_REGIME
    assert normalized["score_admission_eligible"] is True
    assert normalized["historical_scores_used_for_fresh_admission"] is False
    bridge = audit["v49_fresh_selection_metadata_bridge"]
    assert bridge["candidate_values_changed"] is False
    assert bridge["candidate_roles_changed"] is False
    assert bridge["candidate_fingerprints_changed"] is False
    assert bridge["workload_calls"] == 0


def test_nonclean_replay_remains_fail_closed() -> None:
    adapter = _adapter()
    selection, replay = _inputs()
    replay["technical_failure_count"] = 1
    v49.install_adapter(adapter)
    try:
        adapter._normalize_fresh_score_metadata(selection, replay)
    except Exception as error:
        assert "requires fresh selection/replay" in str(error)
    else:
        raise AssertionError("nonclean replay was unexpectedly bridged")


def test_partial_or_conflicting_metadata_remains_fail_closed() -> None:
    adapter = _adapter()
    selection, replay = _inputs()
    selection["score_regime"] = "legacy"
    v49.install_adapter(adapter)
    try:
        adapter._normalize_fresh_score_metadata(selection, replay)
    except Exception as error:
        assert "requires fresh selection/replay" in str(error)
    else:
        raise AssertionError("conflicting metadata was unexpectedly bridged")
