from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seal(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        digest + "  " + path.name + "\n", encoding="ascii")


def fp(signature):
    return {
        "parameter_signature": signature,
        "l1": {"operators": ["scan"], "join_order": []},
        "l2": {"workers": 1, "hash_batches": 1, "spill": 0},
        "l3": {
            "shared_read_blocks": 1, "shared_write_blocks": 0,
            "local_read_blocks": 0, "local_write_blocks": 0,
            "temp_read_blocks": 0, "temp_write_blocks": 0,
            "io_wait_ms": 1,
        },
    }


def test_gradient_event_recomputes_original_formula_and_eta_delta():
    mod = load("shell/mysql_xp_v26_algorithm_c_round_producer_v1.py", "xp_prod_math")
    parameter = mod.PARAMETERS[0]
    baseline = {name: 1.0 for name in mod.PARAMETERS}
    ranges = {name: {"min_value": 0.1, "max_value": 10.0} for name in mod.PARAMETERS}
    row = {
        "subset_id": "s1", "active_subset": [parameter],
        "feature_ratio": {"x": 1.1},
        "predicted_resource": {"cpu": 0.4, "memory": 0.4, "io": 0.5},
        "utilization": {"cpu": 0.4, "memory": 0.4, "io": 0.5},
        "dScore/dU": {parameter: {"cpu": 1.0, "memory": 2.0, "io": -1.0}},
        "T": {parameter: {"cpu": 0.2, "memory": 0.1, "io": 0.3}},
        "confidence": {parameter: {"cpu": 1.0, "memory": 0.5, "io": 0.5}},
        "eta": 0.1, "candidate_score": 7.0,
    }
    event = mod.gradient_event(row, baseline, ranges)
    expected = 1.0 * 0.2 * 1.0 + 2.0 * 0.1 * 0.5 + -1.0 * 0.3 * 0.5
    assert event["g_j"][parameter] == pytest.approx(expected)
    assert event["delta_param"][parameter]["proposed_before_clip"] == pytest.approx(math.exp(0.1 * expected))
    assert event["gradient_source"] == "original_dScore_dU_times_T_times_confidence"


def producer_fixture(tmp_path, monkeypatch):
    mod = load("shell/mysql_xp_v26_algorithm_c_round_producer_v1.py", "xp_prod_full")
    identity = {"database_container_id": "d" * 64,
                "client_container_id": "c" * 64}
    paths = {name: tmp_path / (name + ".json")
             for name in ("adoption", "gate", "r0", "observations", "replays")}
    seal(paths["adoption"], {})
    seal(paths["gate"], {"status": "PASS"})
    seal(paths["r0"], {
        "schema_version": "mysql_xp_fresh_r0_result_v3", "status": "R0_PASS",
        "same_live_identity": identity,
    })
    baseline = {name: 1.0 for name in mod.PARAMETERS}
    ranges = {name: {"min_value": 0.1, "max_value": 10.0} for name in mod.PARAMETERS}
    subsets = []
    fingerprints = []
    index = 0
    for width in range(1, 8):
        for active in itertools.combinations(mod.PARAMETERS, width):
            index += 1
            maps = {
                parameter: {"cpu": 0.1, "memory": -0.2, "io": 0.3}
                for parameter in active
            }
            confidence = {
                parameter: {"cpu": 1.0, "memory": 1.0, "io": 1.0}
                for parameter in active
            }
            subsets.append({
                "subset_id": "subset-%03d" % index,
                "active_subset": list(active),
                "feature_ratio": {"rows": 1.0 + index / 1000.0},
                "predicted_resource": {"cpu": 0.4, "memory": 0.45, "io": 0.5},
                "utilization": {"cpu": 0.4, "memory": 0.45, "io": 0.5},
                "dScore/dU": maps,
                "T": maps,
                "confidence": confidence,
                "eta": 0.01,
                "candidate_score": float(index),
            })
            fingerprints.append(fp("candidate-%03d" % index))
    r0_sha = mod.sha256(paths["r0"])
    gate_sha = mod.sha256(paths["gate"])
    seal(paths["observations"], {
        "schema_version": "mysql_xp_algorithm_c_round_observations_v1",
        "status": "PASS_SEALED_REAL_PLAN_OBSERVATIONS",
        "bundle_id": mod.TARGET_ID, "same_live_identity": identity,
        "fresh_r0_sha256": r0_sha, "dynamic_gate_sha256": gate_sha,
        "qps_resource_input_present": False,
        "replacement_search_algorithm_used": False,
        "baseline_parameters": baseline, "parameter_ranges": ranges,
        "subsets": subsets, "candidate_fingerprints": fingerprints,
    })
    top = list(reversed(subsets))[:5]
    journals = []
    for rank, row in enumerate(top, 1):
        journals.append({
            "rank": rank, "subset_id": row["subset_id"],
            "sealed_candidate_identity": "candidate-sha-%d" % rank,
            "attempt_token": "attempt-token-%d" % rank,
            "started": True, "measurement_complete": True,
            "completion_recorded": True, "real_replay": True,
            "failed_replay": False, "database_counter_timeout_count": 0,
            "qps_used_for_gradient": False,
            "qps_sampled_after_real_replay": True,
            "outer_acceptance": {"qps": 100 + rank},
            "accepted_rounds_credit": 0,
            "fingerprint": fp("replay-%d" % rank),
        })
    seal(paths["replays"], {
        "schema_version": "mysql_xp_algorithm_c_fixed_top5_replays_v1",
        "status": "PASS_FIXED_TOP5_REAL_REPLAYS",
        "bundle_id": mod.TARGET_ID, "same_live_identity": identity,
        "fresh_r0_sha256": r0_sha, "dynamic_gate_sha256": gate_sha,
        "point_journals": journals,
    })
    verified = []
    fake = SimpleNamespace(
        verify_adoption=lambda *unused: ({}, identity),
        load_sealed=lambda path, unused: json.loads(Path(path).read_text()),
        _verify_gate=lambda *unused: None,
        verify_debug=lambda value, *unused: verified.append(value),
    )
    monkeypatch.setattr(mod, "load_target", lambda unused: (tmp_path, fake))
    return mod, paths, verified


def test_producer_enumerates_127_global_top5_and_complete_fingerprints(tmp_path, monkeypatch):
    mod, paths, verified = producer_fixture(tmp_path, monkeypatch)
    candidate_plan = tmp_path / "candidate-plan.json"
    mod.plan(tmp_path, paths["adoption"], paths["gate"], paths["r0"],
             paths["observations"], candidate_plan)
    replay_doc=json.loads(paths["replays"].read_text()); replay_doc["candidate_plan_sha256"]=mod.sha256(candidate_plan)
    seal(paths["replays"],replay_doc)
    output = tmp_path / "debug.json"
    result = mod.produce(tmp_path, paths["adoption"], paths["gate"], paths["r0"],
                         paths["observations"], paths["replays"], output,candidate_plan)
    assert len(result["gradient_events"]) == 127
    assert len(result["global_candidate_ranking"]) == 127
    assert result["global_candidate_ranking"][0]["candidate_score"] == 127.0
    assert [row["subset_id"] for row in result["top5_point_journals"]] == \
           [row["subset_id"] for row in result["global_candidate_ranking"][:5]]
    assert len(result["candidate_fingerprints"]) == 132
    assert result["replacement_search_algorithm_used"] is False
    assert result["accepted_rounds_credit"] == 0
    assert verified == [result]
    project = next(candidate for candidate in (ROOT, *ROOT.parents)
                   if (candidate / "artifacts/mysql_xp_node02_successor_v26").is_dir())
    target_path = (project / "artifacts/mysql_xp_node02_successor_v26"
                   / mod.TARGET_ID / "launch/mysql_xp_node02_formal10_materializer_v1.py")
    spec = importlib.util.spec_from_file_location("xp_v26_real_debug_validator", target_path)
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    target.verify_debug(result, mod.TARGET_ID,
                        {"database_container_id": "d" * 64,
                         "client_container_id": "c" * 64})


def controller_fixture(tmp_path, monkeypatch, accepted_rounds=0, attempted_rounds=0):
    mod = load("shell/mysql_xp_v26_formal10_fail_closed_controller_v1.py", "xp_ctl")
    identity = {"database_container_id": "d" * 64,
                "client_container_id": "c" * 64}
    paths = {name: tmp_path / (name + ".json")
             for name in ("adoption", "gate", "r0", "checkpoint", "debug", "decision")}
    for name in ("adoption", "gate", "r0"):
        seal(paths[name], {})
    checkpoint = {
        "schema_version": "mysql_xp_v26_formal10_fail_closed_checkpoint_v1",
        "status": "RUNNING_NOT_COMPLETE", "bundle_id": mod.TARGET_ID,
        "same_live_identity": identity, "fresh_r0_sha256": mod.sha256(paths["r0"]),
        "attempted_rounds": attempted_rounds, "accepted_rounds": accepted_rounds,
        "accepted_round_ids": ["old-%d" % n for n in range(accepted_rounds)],
        "events": [{"round_id": "old-%d" % n} for n in range(attempted_rounds)],
    }
    seal(paths["checkpoint"], checkpoint)
    journals = [{"subset_id": "subset-%d" % rank,
                 "attempt_token": "token-%d" % rank} for rank in range(1, 6)]
    seal(paths["debug"], {"top5_point_journals": journals})
    fake = SimpleNamespace(verify_debug=lambda *unused: None)
    monkeypatch.setattr(mod, "load_target", lambda unused: fake)
    monkeypatch.setattr(mod, "live_chain", lambda *unused: identity)
    return mod, paths, identity


def decision(mod, paths, identity, attempt, accepted=True):
    value = {
        "schema_version": "mysql_xp_algorithm_c_outer_acceptance_v1",
        "status": "ACCEPTED" if accepted else "REJECTED",
        "bundle_id": mod.TARGET_ID, "same_live_identity": identity,
        "fresh_r0_sha256": mod.sha256(paths["r0"]),
        "dynamic_gate_sha256": mod.sha256(paths["gate"]),
        "debug_round_sha256": mod.sha256(paths["debug"]),
        "round_attempt": attempt, "round_id": "round-%d" % attempt,
        "selected_rank": 1, "selected_subset_id": "subset-1",
        "selected_attempt_token": "token-1", "real_replay": True,
        "failed_replay": False, "database_counter_timeout_count": 0,
        "qps_used_for_gradient": False,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "real_replay_completed_at": "2026-08-03T01:00:00+00:00",
        "qps_measurement_started_at": "2026-08-03T01:00:01+00:00",
        "qps_measurement_completed_at": "2026-08-03T01:03:01+00:00",
        "qps": 100.0, "measurement_seconds": 180,
        "stable_window_reached_at_seconds": 180,
        "accepted": accepted, "accepted_rounds_credit": 1 if accepted else 0,
    }
    seal(paths["decision"], value)


def test_controller_rejection_does_not_increment_and_tenth_accept_completes(tmp_path, monkeypatch):
    mod, paths, identity = controller_fixture(tmp_path, monkeypatch)
    decision(mod, paths, identity, 1, accepted=False)
    rejected = mod.record(tmp_path, paths["adoption"], paths["gate"], paths["r0"],
                          paths["checkpoint"], paths["debug"], paths["decision"],
                          tmp_path / "checkpoint-1.json")
    assert rejected["attempted_rounds"] == 1
    assert rejected["accepted_rounds"] == 0
    assert rejected["status"] == "RUNNING_NOT_COMPLETE"

    other = tmp_path / "other"
    other.mkdir()
    mod, paths, identity = controller_fixture(other, monkeypatch,
                                               accepted_rounds=9,
                                               attempted_rounds=9)
    decision(mod, paths, identity, 10, accepted=True)
    complete = mod.record(other, paths["adoption"], paths["gate"], paths["r0"],
                          paths["checkpoint"], paths["debug"], paths["decision"],
                          other / "checkpoint-10.json")
    assert complete["accepted_rounds"] == 10
    assert complete["status"] == "COMPLETE_10_ACCEPTED_ROUNDS"
    assert complete["accepted_round_authorized"] is True


def test_controller_rejects_qps_before_real_replay_completion(tmp_path, monkeypatch):
    mod, paths, identity = controller_fixture(tmp_path, monkeypatch)
    decision(mod, paths, identity, 1, accepted=True)
    value = json.loads(paths["decision"].read_text())
    value["qps_measurement_started_at"] = "2026-08-03T00:59:59+00:00"
    seal(paths["decision"], value)
    with pytest.raises(mod.ControllerError, match="acceptance evidence"):
        mod.record(tmp_path, paths["adoption"], paths["gate"], paths["r0"],
                   paths["checkpoint"], paths["debug"], paths["decision"],
                   tmp_path / "out.json")


def test_contract_repairs_collector_and_prohibits_v8_replacement():
    contract = json.loads((ROOT / "config/mysql_xp_v26_runtime_producer_contract_v4.json").read_text())
    assert contract["collector_sha256"] == "ac78a618881d8adcb9ed274d4f5eef173242ca893c2b43d9d711a3701ea883c6"
    assert contract["algorithm"]["required_nonempty_subsets"] == 127
    assert contract["algorithm"]["fixed_real_replay_top_k"] == 5
    assert contract["algorithm"]["original_gradient_only"] is True
    assert contract["algorithm"]["replacement_search_algorithm_prohibited"] is True
    assert contract["v8_producer_prohibited"] is True


def test_real_replay_sql_updates_exact_seven_and_flushes():
    mod = load("shell/mysql_xp_v26_real_replay_executor_v1.py", "xp_replay_sql")
    values = {name: index + 0.5 for index, name in enumerate(mod.PARAMETERS)}
    statement = mod.sql(values)
    assert statement.count("UPDATE mysql.") == 7
    assert "FLUSH OPTIMIZER_COSTS" in statement
    assert "--defaults-extra-file" not in statement
    for name in mod.PARAMETERS:
        assert name in statement


def test_real_replay_restores_baseline_in_finally(monkeypatch, tmp_path):
    mod = load("shell/mysql_xp_v26_real_replay_executor_v1.py", "xp_replay_finally")
    identity = {"client_container_id": "c" * 64}
    runtime=tmp_path/"runtime"; (runtime/"bin").mkdir(parents=True); java=runtime/"bin/java"; java.write_bytes(b"java")
    request = {
        "schema_version": "mysql_xp_algorithm_c_real_replay_request_v1",
        "status": "SEALED_FIXED_TOP5_POINT", "bundle_id": mod.TARGET_ID,
        "same_live_identity": identity, "rank": 1, "subset_id": "subset-1",
        "attempt_token": "token-1", "qps_resource_input_present": False,
        "java_sha256": mod.JAVA_SHA256,
        "candidate_parameters": {name: 2.0 for name in mod.PARAMETERS},
        "baseline_parameters": {name: 1.0 for name in mod.PARAMETERS},
    }
    request_path = tmp_path / "request.json"; request_path.write_text(json.dumps(request))
    plan_path=tmp_path/"plan.json"; plan={"schema_version":"mysql_xp_algorithm_c_candidate_plan_v1",
      "status":"SEALED_127_GLOBAL_RANKING_FIXED_TOP5_NOT_REPLAYED","same_live_identity":identity,
      "global_candidate_ranking":[{"subset_id":"subset-1","candidate_parameters":request["candidate_parameters"]}]+[{}]*126}
    plan_path.write_text(json.dumps(plan)); request["candidate_plan_sha256"]="plan-sha"
    target = SimpleNamespace(
        verify_bundle=lambda unused: {"bundle_id": mod.TARGET_ID},
        verify_adoption=lambda *unused: ({"runtime_publication":{"live_tree_readback":{"root":str(runtime)}}}, identity),
        load_sealed=lambda *unused: request,
        sha256=lambda path: "plan-sha" if Path(path)==plan_path else mod.JAVA_SHA256,
        atomic_json=lambda *unused: None,
    )
    loads=iter((request,plan)); target.load_sealed=lambda *unused: next(loads)
    raw = SimpleNamespace(_run=lambda unused: (_ for _ in ()).throw(RuntimeError("workload failed")))
    modules = iter((target, raw)); monkeypatch.setattr(mod, "load_module", lambda *unused: next(modules))
    calls = []; monkeypatch.setattr(mod, "apply", lambda client, values: calls.append(values) or values)
    bundle = tmp_path / mod.TARGET_ID; (bundle / "launch").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="workload failed"):
        mod.execute(bundle, tmp_path / "adoption", request_path, plan_path, java,
                    tmp_path / "readback", tmp_path / "counter", tmp_path / "fp",
                    tmp_path / "stop", tmp_path / "out")
    assert calls == [request["candidate_parameters"], request["baseline_parameters"]]
