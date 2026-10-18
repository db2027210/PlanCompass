#!/usr/bin/env python3
"""v11 static TPC-C spec: inner datadir and generated attempt credential."""
from __future__ import annotations
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_tpcc_v4_bootstrap_spec as base  # noqa:E402

BUNDLE_ID="544f06d3121968dd23e558bfb7ab3f2163d8baabc7f6cc3d500ac3729c8bf599"
base.BUNDLE_ID=BUNDLE_ID
base.BUNDLE_REMOTE=base.RUNTIME_ROOT+"/bundles/"+BUNDLE_ID
base.SERVICE_REMOTE=base.BUNDLE_REMOTE+"/authority/mysql_tpcc_node04_adopted_service_v2.py"
base.MATERIALIZER_REMOTE=base.BUNDLE_REMOTE+"/authority/mysql_tpcc_materialize_phase_command_v2.py"
base.CONFIG_MATERIALIZER_REMOTE=base.BUNDLE_REMOTE+"/authority/mysql_tpcc_config_materialize_v2.py"
MEASUREMENT_BRIDGE_HELPER_REMOTE=base.BUNDLE_REMOTE+"/authority/mysql_tpcc_measurement_bridge_v1.py"

canonical,sha256,load_sidecar,SpecError=base.canonical,base.sha256,base.load_sidecar,base.SpecError
VALIDITY_MINUTES=50
ROOT_REQUIRED_PHASES=(
    "prepare_physical_copy",
    "enforce_private_measurement_tmpfs_after_copy",
    "provision_random_attempt_password_before_config",
    "materialize_attempt_config_before_service_plan",
    "emit_service_plan_only",
    "start_containers_after_main_agent_authorization",
    "enforce_sealed_cgroup_v1_blkio_after_start_before_adopt",
    "adopt_same_live_services_after_blkio_readback",
    "prefault_zero_io_gate",
    "materialize_qualification_only",
    "atomically_request_qualification_stop_after_180s",
    "materialize_typed_capture_after_qualification_gate_only",
)

def _base_compatible_spec(spec):
    value=dict(spec)
    created=datetime.fromisoformat(
        str(value["created_at_utc"]).replace("Z","+00:00")
    ).astimezone(timezone.utc)
    value["expires_at_utc"]=(created+timedelta(minutes=30)).isoformat()
    return value

def validate(spec, *, now=None):
    created=datetime.fromisoformat(
        str(spec["created_at_utc"]).replace("Z","+00:00")
    ).astimezone(timezone.utc)
    expires=datetime.fromisoformat(
        str(spec["expires_at_utc"]).replace("Z","+00:00")
    ).astimezone(timezone.utc)
    current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        expires<=created
        or (expires-created)!=timedelta(minutes=VALIDITY_MINUTES)
        or current>=expires
    ):
        raise SpecError("TPC-C v11 qualification spec validity must be exactly 50 minutes")
    base.validate(_base_compatible_spec(spec),now=current)
    return dict(spec)

def make_spec(manifest_path,attempt_id,token,*,now=None):
    current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    value=base.make_spec(manifest_path,attempt_id,token,now=current)
    value["expires_at_utc"]=(current+timedelta(minutes=VALIDITY_MINUTES)).isoformat()
    return validate(value,now=current)

def validate_plan_bindings(plan):
    commands=plan.get("commands_to_run_on_node04")
    if not isinstance(commands,dict):
        raise SpecError("v11 command map is absent")
    expected_executables={
        "prepare_physical_copy":base.SERVICE_REMOTE,
        "enforce_private_measurement_tmpfs_after_copy":MEASUREMENT_BRIDGE_HELPER_REMOTE,
        "provision_random_attempt_password_before_config":base.CONFIG_MATERIALIZER_REMOTE,
        "materialize_attempt_config_before_service_plan":base.CONFIG_MATERIALIZER_REMOTE,
        "emit_service_plan_only":base.SERVICE_REMOTE,
        "start_containers_after_main_agent_authorization":base.SERVICE_REMOTE,
        "enforce_sealed_cgroup_v1_blkio_after_start_before_adopt":base.BUNDLE_REMOTE+"/authority/mysql_tpcc_blkio_v1.py",
        "adopt_same_live_services_after_blkio_readback":base.SERVICE_REMOTE,
        "prefault_zero_io_gate":base.SERVICE_REMOTE,
        "materialize_qualification_only":base.MATERIALIZER_REMOTE,
        "atomically_request_qualification_stop_after_180s":
            base.MATERIALIZER_REMOTE,
        "materialize_typed_capture_after_qualification_gate_only":base.MATERIALIZER_REMOTE,
    }
    for name,executable in expected_executables.items():
        argv=commands.get(name)
        if not isinstance(argv,list) or executable not in argv:
            raise SpecError("v11 executable binding differs: "+name)
        if name in ROOT_REQUIRED_PHASES and argv[:2] != ["sudo","-n"]:
            raise SpecError("v11 root-required phase lacks sudo -n: "+name)
    outputs={}
    for name in (
        "enforce_private_measurement_tmpfs_after_copy",
        "start_containers_after_main_agent_authorization",
        "enforce_sealed_cgroup_v1_blkio_after_start_before_adopt",
        "adopt_same_live_services_after_blkio_readback",
        "prefault_zero_io_gate",
    ):
        argv=commands[name]
        if "--output" not in argv:
            raise SpecError("v11 phase output binding is absent: "+name)
        outputs[name]=argv[argv.index("--output")+1]
    expected_suffixes={
        "enforce_private_measurement_tmpfs_after_copy":"/measurement_bridge_authority.json",
        "start_containers_after_main_agent_authorization":"/start_authority.json",
        "enforce_sealed_cgroup_v1_blkio_after_start_before_adopt":"/blkio_throttle_authority.json",
        "adopt_same_live_services_after_blkio_readback":"/adopted_service.json",
        "prefault_zero_io_gate":"/prefault_zero_io.json",
    }
    if len(set(outputs.values()))!=5 or any(
        not outputs[name].endswith(suffix)
        for name,suffix in expected_suffixes.items()
    ):
        raise SpecError("v11 bridge/start/blkio/adopt/prefault outputs are not independent")
    payload=canonical(plan).decode("ascii")
    invalidated={
        "1954fa4bfd4401a9026638926dee7690c0f7c44c1b9628380dffad2b8807d51c",
        "0068f06905d2f9c3fa71a8ecd573ed597846702e1d7733466c9c52c06a395b35",
        "48abd6390d7f0d87f8c237830e0dfcbef7f9a7218ca2a42c3ec0cef634851344",
        "7ec89c2ea72b47d2798efacf106560b34e3308eda64c12a5e84555a212d4bf17",
        "acb45d8886729d8fd389197ba1ba3b21cdc03ceeebc0690f7e87fb5111638d84",
        "45ada2d5a371b96f8349b4c69b458f4cc020322da94f0a3081cb4a9f423484f1",
        "d82884f3ed79ef1491beb0a405df4b7933ec7142b5a7612dda035f4849895fdf",
        "c58caf2abc6fab87b9ca73b7e44e565ee87d8f75866a369b806f43c4ddce37dc",
        "96e0bde80a06c85c56417e465b100b839b99f7d2c53b881e12f88bb0d74d4033",
        "51886595b82626501afcbd19016c586f3e3a400f5ea8f66af468bd1a6f85ea52",
        "d464e4839501810a91608511403b48ec049fa5f5079b52af1386bcbd870b2e8d",
        "1c55eda363e52e23ee49bda922c18d0e3a907b66867074d5326b42e0838d4eb8",
        "0625ad06f3742df414f4138125b10b4e4f536ca568141ec7a288c3d096042bcb",
    }
    if any(bundle_id in payload for bundle_id in invalidated):
        raise SpecError("invalidated executable path remains")
    prefix=base.RUNTIME_ROOT+"/bundles/"
    fragments=payload.split(prefix)[1:]
    observed={fragment[:64] for fragment in fragments}
    if observed!={BUNDLE_ID}:
        raise SpecError("v11 plan references another executable bundle")
    return plan

def command_plan(spec):
    spec=validate(spec)
    plan=base.command_plan(_base_compatible_spec(spec)); paths=spec["paths"]; inner=paths["datadir"]+"/datadir"
    commands=plan["commands_to_run_on_node04"]
    copy=commands["prepare_physical_copy"]
    copy.extend(["--image-id",base.IMAGE_ID])
    commands["enforce_private_measurement_tmpfs_after_copy"]=[
        "sudo","-n",base.HOST_PYTHON,MEASUREMENT_BRIDGE_HELPER_REMOTE,
        "enforce","--path",paths["measurement_bridge"],
        "--attempt-id",spec["attempt"]["id"],"--bundle-id",BUNDLE_ID,
        "--output",paths["attempt_root"]+"/measurement_bridge_authority.json",
    ]
    service=commands["emit_service_plan_only"]
    service[service.index("--datadir")+1]=inner
    commands["provision_random_attempt_password_before_config"]=[base.HOST_PYTHON,base.CONFIG_MATERIALIZER_REMOTE,"provision-password","--bundle",base.BUNDLE_REMOTE,"--image-id",base.IMAGE_ID,"--attempt-id",spec["attempt"]["id"],"--datadir",inner,"--mysql-password-file",spec["attempt"]["mysql_password_file"],"--output",paths["attempt_root"]+"/credential_provisioning.json"]
    commands["materialize_attempt_config_before_service_plan"][2]="materialize"
    start=commands.pop("bootstrap_after_main_agent_authorization")
    start[start.index("bootstrap")]="start"
    start[start.index("--output")+1]=paths["attempt_root"]+"/start_authority.json"
    adopt=commands.pop("adopt_readback")
    prefault=commands.pop("prefault_zero_io_gate")
    commands["start_containers_after_main_agent_authorization"]=start
    commands["enforce_sealed_cgroup_v1_blkio_after_start_before_adopt"]=["sudo","-n",base.HOST_PYTHON,base.BUNDLE_REMOTE+"/authority/mysql_tpcc_blkio_v1.py","enforce","--plan",paths["service_plan"],"--output",paths["attempt_root"]+"/blkio_throttle_authority.json"]
    commands["adopt_same_live_services_after_blkio_readback"]=adopt
    commands["prefault_zero_io_gate"]=prefault
    commands["atomically_request_qualification_stop_after_180s"]=[
        "sudo","-n",base.HOST_PYTHON,base.MATERIALIZER_REMOTE,
        "write-stop","--bundle",base.BUNDLE_REMOTE,
        "--service",paths["attempt_root"]+"/adopted_service.json",
        "--phase","qualification","--seconds","180",
    ]
    for name in ROOT_REQUIRED_PHASES:
        argv=commands[name]
        if argv[:2] != ["sudo","-n"]:
            commands[name]=["sudo","-n",*argv]
    ordered=("prepare_physical_copy","enforce_private_measurement_tmpfs_after_copy","provision_random_attempt_password_before_config","materialize_attempt_config_before_service_plan","emit_service_plan_only","start_containers_after_main_agent_authorization","enforce_sealed_cgroup_v1_blkio_after_start_before_adopt","adopt_same_live_services_after_blkio_readback","prefault_zero_io_gate")
    plan["commands_to_run_on_node04"]={**{name:commands[name] for name in ordered},**{name:value for name,value in commands.items() if name not in ordered}}
    plan["schema_version"]="mysql_tpcc_v5_static_emit_plan_v1";plan["inner_mysql_datadir"]=inner
    plan["required_preconditions"].insert(3,"sealed_random_attempt_password_skip_grant_tables_skip_networking_PASS")
    plan["required_preconditions"].insert(4,"sealed_host_root_cgroup_v1_blkio_259_2_1205862400_PASS_after_start_before_adopt")
    plan["required_preconditions"].insert(5,"sealed_host_root_measurement_tmpfs_1008_1008_0700_PASS_before_service_plan")
    return validate_plan_bindings(plan)

def main():
    # Reuse v4 rendering/sidecar rules; emit-plan is overridden to v11 argv.
    import argparse
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument("--render",action="store_true");g.add_argument("--emit-plan",type=Path);p.add_argument("--bundle-manifest",type=Path);p.add_argument("--attempt-id");p.add_argument("--main-agent-token-file",type=Path);p.add_argument("--output",type=Path);a=p.parse_args()
    if a.emit_plan:
        plan=command_plan(load_sidecar(a.emit_plan))
        if a.output:
            payload=canonical(plan);base.atomic(a.output,payload);base.atomic(Path(str(a.output)+".sha256"),(sha256(a.output)+"  "+a.output.name+"\n").encode())
        print(json.dumps({"status":"PASS_EMITTED_QUALIFICATION_ONLY_PLAN","bundle_id":BUNDLE_ID,"output":str(a.output) if a.output else None},sort_keys=True));return 0
    if not all((a.bundle_manifest,a.attempt_id,a.main_agent_token_file,a.output)):raise SpecError("render arguments absent")
    raw=a.main_agent_token_file
    if raw.is_symlink() or not raw.is_file() or raw.stat().st_mode&0o077:raise SpecError("main-agent token file must be regular 0600")
    token=raw.read_text().strip();doc=make_spec(a.bundle_manifest,a.attempt_id,token);payload=canonical(doc);base.atomic(a.output,payload);base.atomic(Path(str(a.output)+".sha256"),(sha256(a.output)+"  "+a.output.name+"\n").encode());print(json.dumps({"status":"PASS_RENDERED_QUALIFICATION_ONLY_SPEC","bundle_id":BUNDLE_ID},sort_keys=True));return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except (OSError,ValueError,SpecError) as exc:sys.stderr.write("NOT_READY: "+str(exc)+"\n");raise SystemExit(2)
