from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE=Path(__file__).resolve();BUNDLE=HERE.parents[1];BUNDLED=(BUNDLE/"bundle_manifest.json").is_file();PROJECT=BUNDLE.parents[2] if BUNDLED else BUNDLE
def path(relative):
    if BUNDLED:return BUNDLE/relative
    return PROJECT/("shell/"+Path(relative).name)
def load(name,relative):
    p=path(relative);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def inputs(c):
    planner={"container_name":"job_db","container_id":"a"*64,"pid":101,"datadir":"/mnt/mysql_nvme1n1/job/x","database_inspect_config_sha256":"b"*64}
    fresh={"schema_version":"mysql_job_distributed_v2_fresh_r0_evidence_v1","status":"PASS_FRESH_R0","attempt_token":"job-v14-"+"x"*32,"planner_identity":planner,"accepted_rounds_credit":0,"accepted_round_authorized":False}
    debug={"schema_version":"mysql_job_distributed_v2_debug_round_evidence_v1","status":"PASS_DEBUG_ROUND","attempt_token":fresh["attempt_token"],"planner_identity":planner,"completed_nonempty_subsets":15,"fixed_top5_only":True,"fingerprint_l1_l2_l3_complete":True,"accepted_rounds_credit":0,"algorithm_formula":c.FORMULA}
    handoff={"schema_version":"mysql_job_distributed_v2_same_live_planner_handoff_v1","status":"READY_FORMAL10_SAME_LIVE_PLANNER","stage":"formal10","bundle_id":c.TARGET_ID,"attempt_token":fresh["attempt_token"],"planner_identity":planner,"formal10_authorized":True,"target_accepted_rounds":10,"outer_acceptance_authorized":True,"accepted_round_authorized":False,"capture_or_r0_or_debug_countable_as_accepted":False,"expires_at_utc":(datetime.now(timezone.utc)+timedelta(minutes=14)).isoformat()}
    baseline={"qps":100.0,"db_qps":100.0,"db_qps_source":"database_status_counter","p95_ms":10.0,"p99_ms":20.0,"utilization_pct":{"cpu":.4,"memory":.9,"io":.4}}
    target=PROJECT/"artifacts/mysql_job_native_duration_successor_v14"/c.TARGET_ID
    control=PROJECT/"artifacts/mysql_job_v14_duration_control_successor_v1"/c.CONTROL_ID
    post=PROJECT/"artifacts/mysql_job_post_r0_debug_successor_v1"/c.POST_ID
    base=json.loads((target/"predecessor/config.json").read_text());base.update({"target_bundle_id":c.TARGET_ID,"target_manifest_sha256":c.TARGET_SHA,"support_bundle_id":c.CONTROL_ID,"support_manifest_sha256":c.CONTROL_SHA,"accepted_round_authorized":False,"exact_service_module":str(post/"campaign/mysql_job_post_r0_exact_services_v1.py")})
    return fresh,debug,handoff,baseline,base


def result(c,state,authority,accepted=True,index=1):
    evaluations=[]
    for rank in range(1,6):
        evaluations.append({"candidate_rank":rank,"candidate_subset_id":"s%d"%rank,"candidate_final_validation":{"qps":101.0,"db_qps":101.0,"db_qps_source":"database_status_counter","evidence":{"requested_effective_costs":"cost.json","fresh_connection_generation":"generation.json","stability_windows_and_coverage":{"status":"PASS","measurement_seconds":600,"sample_count":600}}},"recovery":{"verified":True}})
    before=state["accepted_rounds"];after=before+(1 if accepted else 0)
    record={"accepted":accepted,"accepted_round_authorized":True,"atomic_accounting_committed":accepted,"accepted_accounting_token":("token-%d"%index if accepted else None),"accepted_shrink_count":after,"incumbent_after":{name:float(index) for name in c.PARAMETERS},"incumbent_validation_after":{"qps":101.0,"db_qps":101.0,"db_qps_source":"database_status_counter","p95_ms":9.0,"p99_ms":19.0,"utilization_pct":{"cpu":.4,"memory":.8,"io":.4}},"range_after":state["current_ranges"]}
    return {"schema_version":"mysql_job_formal10_attempt_result_v1","formal_attempt_id":authority["formal_attempt_id"],"post_r0_bundle_id":c.POST_ID,"qps_role":"outer_acceptance_after_real_replay_only","outer_loop":[{"campaign_round":record,"real_replay_orchestration":{"evaluations":evaluations,"updated_sensitivity_table":state["current_sensitivity"]}}],"result":{"accepted_shrink_count":after,"target_accepted_shrinks":10}}


def test_audit_detects_missing_formal10_in_post_bundle():
    post=PROJECT/"artifacts/mysql_job_post_r0_debug_successor_v1/0aa28209af781a16bd576f81147298d40fe31f3f2c3c6975edbe18843d07e7ad/bundle_manifest.json"
    entries=(json.loads(post.read_text()).get("entrypoints") or {})
    assert "formal10_controller" not in entries


def test_state_authority_and_rejection_zero_accepted(tmp_path):
    c=load("formal_controller","controller/mysql_job_formal10_controller_v1.py");fresh,debug,handoff,baseline,base=inputs(c)
    state=c.initial_state(fresh,debug,handoff,baseline,base)
    handoff_path=tmp_path/"handoff.json";handoff_path.write_bytes(c.canonical(handoff))
    launch=c.authorize(state,handoff,fresh,debug,base,tmp_path/"attempt",900,handoff_path)
    authority=json.loads((tmp_path/"attempt/main_agent_authority.json").read_text());token=(tmp_path/"attempt/main_agent_token").read_text().strip()
    assert authority["authorized_accepted_round"]==1 and authority["one_outer_attempt_only"] is True
    assert (tmp_path/"attempt/main_agent_token").stat().st_mode & 0o777 == 0o600
    updated=c.commit(state,authority,token,result(c,state,authority,False))
    assert updated["accepted_rounds"]==0 and updated["rejected_rounds"]==1 and updated["next_accepted_round"]==1
    assert "mysql_job_formal10_campaign_v1.py" in " ".join(launch["argv"])


def test_ten_atomic_accepts_complete_exactly(tmp_path):
    c=load("formal_controller_10","controller/mysql_job_formal10_controller_v1.py");fresh,debug,handoff,baseline,base=inputs(c);state=c.initial_state(fresh,debug,handoff,baseline,base)
    for index in range(1,11):
        token="secret-%d"%index;authority={"formal_attempt_id":"attempt-%d"%index,"state_sha256":hashlib.sha256(c.canonical(state)).hexdigest(),"main_agent_token_sha256":hashlib.sha256(token.encode()).hexdigest(),"expires_at_utc":(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
        state=c.commit(state,authority,token,result(c,state,authority,True,index))
        assert state["accepted_rounds"]==index
    assert state["status"]=="PASS_FORMAL10_COMPLETE" and state["formal_complete"] is True
    with pytest.raises(c.Formal10Error):c.commit(state,authority,token,result(c,state,authority,True,11))


def test_technical_or_incomplete_replay_never_commits(tmp_path):
    c=load("formal_controller_fail","controller/mysql_job_formal10_controller_v1.py");fresh,debug,handoff,baseline,base=inputs(c);state=c.initial_state(fresh,debug,handoff,baseline,base)
    token="secret";authority={"formal_attempt_id":"attempt-fail","state_sha256":hashlib.sha256(c.canonical(state)).hexdigest(),"main_agent_token_sha256":hashlib.sha256(token.encode()).hexdigest(),"expires_at_utc":(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()};bad=result(c,state,authority,True);bad["outer_loop"][0]["real_replay_orchestration"]["evaluations"][0]["callback_error"]="timeout"
    with pytest.raises(c.Formal10Error):c.commit(state,authority,token,bad)
    assert state["accepted_rounds"]==0 and state["attempts_committed"]==0


def test_campaign_rejects_non_600_180_or_public_token(tmp_path):
    r=load("formal_campaign","campaign/mysql_job_formal10_campaign_v1.py")
    config={"formal_attempt_id":"a","formal_state_sha256":"s","accepted_round_authorized":True,"measurement_seconds":599,"minimum_measurement_seconds":180,"formal_outer_protocol":{"max_outer_attempts":1,"target_accepted_shrinks":10}}
    token=tmp_path/"token";token.write_text("secret");token.chmod(0o644)
    authority={"schema_version":"mysql_job_formal10_attempt_authority_v1"}
    with pytest.raises(r.CampaignError):r.validate(config,authority,token)


def test_transactional_publisher_closes_and_is_idempotent(tmp_path):
    publisher=load("formal_publisher","publisher/mysql_content_addressed_bundle_publish_v1.py")
    if not BUNDLED:
        pytest.skip("publisher closure requires the built content-addressed bundle")
    bundle_id=BUNDLE.name
    destination_root=tmp_path/"published"
    destination_root.mkdir()
    first=publisher.publish(BUNDLE,destination_root,bundle_id)
    assert first["status"]=="PASS_ATOMICALLY_PUBLISHED"
    published=destination_root/bundle_id
    assert publisher.verify(published,bundle_id)["bundle_id"]==bundle_id
    second=publisher.publish(BUNDLE,destination_root,bundle_id)
    assert second["status"]=="PASS_ALREADY_PUBLISHED_IDENTICAL"
