from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
BUNDLED = (BUNDLE_ROOT / "bundle_manifest.json").is_file()
ROOT = BUNDLE_ROOT.parents[2] if BUNDLED else BUNDLE_ROOT
TARGET = ROOT / "artifacts/mysql_job_native_duration_successor_v14/659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
SUPPORT = ROOT / "artifacts/mysql_job_v14_duration_control_successor_v1/ec061f7b186af215e14daaaac64333fa3e88c8f934f78fe055d1f3ef091ace0b"


def component(relative: str) -> Path:
    if BUNDLED:
        return BUNDLE_ROOT / relative
    mapping = {
        "campaign/mysql_job_post_r0_debug_evidence_v1.py": "shell/mysql_job_post_r0_debug_evidence_v1.py",
        "campaign/mysql_job_post_r0_exact_services_v1.py": "shell/mysql_job_post_r0_exact_services_v1.py",
        "campaign/mysql_job_post_r0_debug_campaign_v1.py": "shell/mysql_job_post_r0_debug_campaign_v1.py",
        "materialize/mysql_job_post_r0_debug_materializer_v1.py": "shell/mysql_job_post_r0_debug_materializer_v1.py",
        "authority/build_mysql_job_post_r0_debug_successor_v1.py": "shell/build_mysql_job_post_r0_debug_successor_v1.py",
    }
    return ROOT / mapping[relative]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def fresh_inputs():
    planner = {"container_id": "a" * 64, "pid": 123, "datadir": "/data"}
    fresh = {
        "schema_version": "mysql_job_distributed_v2_fresh_r0_evidence_v1",
        "status": "PASS_FRESH_R0", "bundle_id": TARGET.name,
        "attempt_token": "attempt-1", "planner_identity": planner,
        "accepted_rounds_credit": 0, "accepted_round_authorized": False,
        "candidate_credit": 0, "top5_replay_credit": 0,
    }
    handoff = {
        "schema_version": "mysql_job_distributed_v2_same_live_planner_handoff_v1",
        "status": "READY_DEBUG_SAME_LIVE_PLANNER", "stage": "debug",
        "attempt_token": "attempt-1", "planner_identity": planner,
        "accepted_round_authorized": False, "formal10_authorized": False,
    }
    gate = {
        "schema_version": "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status": "PASS", "attempt_token": "attempt-1", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "accepted_round_authorized": False,
    }
    return fresh, handoff, gate


def raw_debug(fresh):
    params = (
        "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
        "memory_temptable_row_cost",
    )
    events = []
    for index, size_members in enumerate(
        (combo for size in range(1, 5) for combo in itertools.combinations(params, size)), 1
    ):
        events.append({
            "subset_id": f"subset_{index:04d}", "subset_parameters": list(size_members),
            "feature_ratio": {"memory": 1.0}, "predicted_resource": {"memory": .9},
            "utilization": {"memory": .9}, "dScore/dU": {"memory": 1.0},
            "T": {"memory": .1}, "confidence": 1.0, "g_j": .1,
            "eta": .0051, "delta_param": .01, "candidate_score": float(16-index),
        })
    return {
        "schema_version": "mysql_job_post_r0_raw_debug_round_v1",
        "status": "PASS_RAW_DEBUG_ROUND", "attempt_token": fresh["attempt_token"],
        "planner_identity": fresh["planner_identity"],
        "algorithm_formula": "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
        "qps_role": "outer_acceptance_after_real_replay_only",
        "accepted_rounds_credit": 0, "accepted_round_authorized": False,
        "gradient_events": events,
        "global_candidate_ranking": [
            {"rank": i, "subset_id": row["subset_id"], "candidate_score": row["candidate_score"]}
            for i, row in enumerate(events, 1)
        ],
        "real_replay_top5": [
            {"rank": i, "subset_id": events[i-1]["subset_id"], "status": "PASS_REAL_REPLAY",
             "fingerprint": {"l1": "x", "l2": {"rows": i}, "l3": {"memory": i}},
             "qps_used_in_candidate_score": False}
            for i in range(1, 6)
        ],
    }


def test_debug_evidence_accepts_exact_15_and_fixed_top5():
    module = load("job_evidence", component("campaign/mysql_job_post_r0_debug_evidence_v1.py"))
    fresh, _, _ = fresh_inputs()
    result = module.build_evidence(raw_debug(fresh), fresh)
    assert result["status"] == "PASS_DEBUG_ROUND"
    assert result["completed_nonempty_subsets"] == 15
    assert result["replayed_top5_ranks"] == [1, 2, 3, 4, 5]
    assert result["accepted_rounds_credit"] == 0


def test_debug_evidence_rejects_missing_subset_and_missing_l3():
    module = load("job_evidence_bad", component("campaign/mysql_job_post_r0_debug_evidence_v1.py"))
    fresh, _, _ = fresh_inputs(); raw = raw_debug(fresh)
    raw["gradient_events"].pop()
    with pytest.raises(module.EvidenceError): module.build_evidence(raw, fresh)
    raw = raw_debug(fresh); raw["real_replay_top5"][0]["fingerprint"]["l3"] = None
    with pytest.raises(module.EvidenceError): module.build_evidence(raw, fresh)


def test_materializer_preserves_6b55_and_token_is_file_only(tmp_path):
    module = load("job_materializer", component("materialize/mysql_job_post_r0_debug_materializer_v1.py"))
    fresh, handoff, gate = fresh_inputs()
    successor = tmp_path / "successor"; (successor / "campaign").mkdir(parents=True)
    output = tmp_path / "out"
    config, authority, token, launch = module.build_materials(
        successor=successor, target=TARGET, support=SUPPORT, fresh_r0=fresh,
        handoff=handoff, handoff_path=tmp_path / "debug-handoff.json",
        gate=gate, baseline_path=tmp_path / "baseline.json",
        output_directory=output, ttl_seconds=600,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    assert config["support_bundle_id"] == module.SUPPORT_ID
    assert config["qualification_support_predecessor_bundle_id"] == module.SUPPORT_PREDECESSOR_ID
    assert config["shared_dispatch_sha256"] == module.DISPATCH_SHA256
    assert config["formal_outer_protocol"]["formal_search_initial_replay_top_k"] == 5
    assert config["formal_outer_protocol"]["formal_search_max_replay_top_k"] == 5
    assert config["same_live_planner_authority"]["bundle_id"] == TARGET.name
    assert config["distributed_v2"]["bundle_manifest_sha256"] == module.TARGET_MANIFEST_SHA256
    assert token not in json.dumps(launch)
    assert "--main-agent-token-file" in launch["argv"]
    assert authority["accepted_rounds_credit"] == 0
    assert authority["current_config_sha256"] == hashlib.sha256(module.canonical(config)).hexdigest()


def test_exact_service_overrides_both_campaign_types():
    sys.path.insert(0, str(TARGET / "qualification"))
    try:
        module = load("job_exact_services", component("campaign/mysql_job_post_r0_exact_services_v1.py"))
        assert "_apply_exact" in module.ExactAdoptedPlannerCampaign.apply_candidate_costs.__code__.co_names
        assert "_apply_exact" in module.ExactV2ReplayCampaign.apply_candidate_costs.__code__.co_names
        source = component("campaign/mysql_job_post_r0_exact_services_v1.py").read_text()
        assert "campaign.owner.client_for(campaign)" in source
        assert "super().apply_candidate_costs()" not in source
    finally:
        sys.path.pop(0)


def test_campaign_rejects_public_token_file(tmp_path):
    module = load("job_campaign", component("campaign/mysql_job_post_r0_debug_campaign_v1.py"))
    token = tmp_path / "token"; token.write_text("secret\n"); token.chmod(0o644)
    config = {"attempt_token": "attempt-1"}
    authority = {
        "schema_version": "mysql_job_post_r0_debug_authority_v1",
        "attempt_token": "attempt-1", "main_agent_token_sha256": hashlib.sha256(b"secret").hexdigest(),
        "accepted_round_authorized": False, "formal_launch_authorized": False,
        "debug_round_authorized": True,
        "not_before_utc": "2026-08-03T00:00:00+00:00", "expires_at_utc": "2099-08-03T00:00:00+00:00",
    }
    with pytest.raises(module.CampaignError): module._validate_authority(config, authority, token)


def test_builder_declares_all_four_entrypoints():
    source = component("authority/build_mysql_job_post_r0_debug_successor_v1.py").read_text()
    for name in ("materializer", "campaign_cli", "exact_services", "pass_debug_round_producer"):
        assert f'"{name}"' in source
    assert '"all_nonempty_subsets": 15' in source
    assert '"accepted_rounds_credit": 0' in source
