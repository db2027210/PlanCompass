from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import build_tpcc_v47_trusted3_serial_compat as builder
from tools import run_tpcc_v47_autonomous_trusted3_loop as controller
from tools import run_tpcc_v47_plan_regime_trusted3_strict_serial as runner


PROJECT = Path(__file__).resolve().parents[1]
V46 = PROJECT / "history/tpcc_v46_zero_call_postrun_absorption_package_20260803"
V46_SELECTION = V46 / "next_top5_trusted3_probe2.json"
V46_MANIFEST = V46 / "manifest.json"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"
V47 = PROJECT / "history/tpcc_v47_trusted3_serial_compat_package_20260804"
V47_SELECTION = V47 / "top5.json"
RUNTIME = (
    PROJECT
    / "history/tpcc_v40_clean5_exact_recalibration_package_20260803/runtime"
)
V46_SELECTION_SHA = "e1e1d9ec002f4276cc96194af4644968e13e1bf12b5ec14fe2a9246f3cc24a18"
V46_MANIFEST_SHA = "94aa1f13670d0d8168093d80f51bbd78212225d5573ee481d7809a78285d28ff"
FORMAL_SHA = "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0"


def _build() -> dict:
    return builder.build(
        source_selection_path=V46_SELECTION,
        source_selection_sha256=V46_SELECTION_SHA,
        source_manifest_path=V46_MANIFEST,
        source_manifest_sha256=V46_MANIFEST_SHA,
        formal_state_path=FORMAL,
        formal_state_sha256=FORMAL_SHA,
    )


def test_v46_authority_hashes_remain_unchanged() -> None:
    assert builder._sha(V46_SELECTION) == V46_SELECTION_SHA
    assert builder._sha(V46_MANIFEST) == V46_MANIFEST_SHA
    assert builder._sha(FORMAL) == FORMAL_SHA


def test_v47_changes_only_routing_metadata() -> None:
    source = json.loads(V46_SELECTION.read_text(encoding="utf-8"))
    result = _build()
    assert result["top5"] == source["top5"]
    assert result["selection_contract"] == builder.SELECTION_CONTRACT
    contract = result["v47_compatibility_contract"]
    assert contract["contract"] == builder.COMPATIBILITY_CONTRACT
    assert contract["candidate_payload_changed"] is False
    assert contract["candidate_roles_changed"] is False
    assert contract["candidate_scores_changed"] is False
    assert contract["candidate_fingerprints_changed"] is False
    assert contract["workload_calls_added"] == 0
    assert result["state_mutation_contract"] == builder.STATE_MUTATION_NONE


def test_v47_runtime_accepts_trusted3_and_rejects_compat_tamper() -> None:
    analysis = json.loads(V47_SELECTION.read_text(encoding="utf-8"))
    runner._validate_v44(analysis, analysis["top5"])
    runner._validate_v47(analysis, analysis["top5"])
    tampered = copy.deepcopy(analysis)
    tampered["v47_compatibility_contract"]["candidate_payload_changed"] = True
    with pytest.raises(RuntimeError, match="compatibility contract mismatch"):
        runner._validate_v47(tampered, tampered["top5"])


def test_full_controller_verify_only_uses_v47_route_and_adds_no_files() -> None:
    before = sorted(path.relative_to(V47) for path in V47.rglob("*"))
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        (str(RUNTIME), str(PROJECT / "tools"), str(PROJECT))
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-u",
            str(Path(controller.__file__).resolve()),
            "--selection",
            str(V47_SELECTION),
            "--campaign-root",
            str(V47),
            "--expected-formal-state-sha256",
            FORMAL_SHA,
            "--verify-only",
        ],
        cwd=PROJECT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout
    assert "tpcc_v47_autonomous_trusted3_preflight_passed" in completed.stdout
    assert '"workload_calls_added": 0' in completed.stdout
    after = sorted(path.relative_to(V47) for path in V47.rglob("*"))
    assert after == before


def test_v47_paths_are_independent() -> None:
    replay, ledger, events, summary = controller._paths(V47_SELECTION, 1)
    assert replay.name == "top5_real_replay_round17_v47_trusted3_diagnostic_02"
    assert ledger.name == "strict_top5_real_replay_round17_v47_ledger_v1.json"
    assert events == replay / "events.jsonl"
    assert summary == replay / "summary.json"
