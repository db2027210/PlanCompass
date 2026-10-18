from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
specfile = ROOT / "shell/mysql_xp_v3_bootstrap_spec.py"
loader = importlib.util.spec_from_file_location("xp_spec", specfile)
module = importlib.util.module_from_spec(loader); loader.loader.exec_module(module)

def good():
    now = datetime.now(timezone.utc)
    attempt = "xp-v3-" + "a" * 24
    root = module.ROOT + "/" + attempt
    return {"schema_version":"mysql_xp_v3_short_lived_bootstrap_spec_v1", "formal_scope":"mysql_only", "cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True, "scenario":"hybench_xp_io", "host":"node02", "bundle":{"id":module.BUNDLE_ID,"path":module.BUNDLE,"manifest_sha256":"577f86de52e7216a4c08c0ae8a581a115c861bd847718ba4e70d50b767327fa8"}, "attempt":{"id":attempt,"handoff_token":attempt,"main_agent_token_sha256":hashlib.sha256(b"x").hexdigest()}, "jdk":{"source":module.JDK_SOURCE,"target":module.JDK_TARGET,"tree_sha256":module.JDK_TREE,"java_sha256":module.JAVA_SHA,"publication":root+"/jdk_publication.json"}, "images":{"database":{"reference":module.MYSQL_IMAGE_REFERENCE,"image_id":module.MYSQL_IMAGE_ID},"client":{"reference":module.MYSQL_IMAGE_REFERENCE,"image_id":module.MYSQL_IMAGE_ID}}, "paths":{"config_dir":module.BUNDLE+"/predecessor/predecessor/config","datadir":root+"/datadir","host_output_tmpfs":root+"/host_measurement_tmpfs","copy_authority":root+"/physical_copy_authority.json","adoption":root+"/adopted_service.json"}, "created_at_utc":now.isoformat(),"expires_at_utc":(now+timedelta(minutes=20)).isoformat(),"qualification_only":True,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False}

def test_valid_spec_yields_local_only_exact_argv():
    plan = module.command_plan(good())
    assert plan["ssh_prohibited"] is True
    assert plan["formal_launch_authorized"] is False
    assert plan["commands_to_run_on_node02"]["bootstrap"][7].endswith("mysql_xp_node02_adopted_service_v1.py")
    assert plan["expected_readbacks"]["bridge"][0:2] == ["docker", "exec"]
    assert plan["expected_readbacks"]["data_partition_major_minor"] == "259:4"
    assert plan["expected_readbacks"]["io_throttle_parent_major_minor"] == "259:1"
    assert plan["expected_readbacks"][
        "unique_partition_and_parent_exclusivity_gate"
    ] is True
    assert "--authorization-expires-at-utc" in plan["commands_to_run_on_node02"]["bootstrap"]
    assert plan["execution_order"][0] == "physical_copy"
    assert plan["host_control_cpu_affinity"] == "21-33,57-69"
    assert plan["physical_copy_effective_uid_required"] == 0
    assert plan["expected_readbacks"]["attempt_datadir_owner"] == {
        "uid": 999, "gid": 999,
    }
    assert plan["expected_readbacks"]["attempt_datadir_directory_mode"] == 0o700
    assert plan["expected_readbacks"]["attempt_datadir_regular_mode"] == 0o600
    assert plan["expected_readbacks"][
        "source_metadata_before_after_unchanged"
    ] is True
    assert plan["root_helper_host_python_contract"]["argv_prefix"] == [
        "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B",
    ]
    assert plan["expected_readbacks"]["sealed_bundle_unchanged"] is True
    assert plan["expected_readbacks"]["sealed_bundle_write_count"] == 0
    assert plan["expected_readbacks"]["sealed_bundle_bytecode_artifacts"] == []
    for command in plan["commands_to_run_on_node02"].values():
        assert command[3:7] == [
            "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B",
        ]
    assert "--reflink=never" not in str(plan)

def test_main_agent_can_emit_a_valid_short_lived_spec():
    spec = module.make_spec(
        "xp-v3-" + "z" * 24, hashlib.sha256(b"main").hexdigest(),
        module.MYSQL_IMAGE_REFERENCE, module.MYSQL_IMAGE_ID,
        module.MYSQL_IMAGE_REFERENCE, module.MYSQL_IMAGE_ID,
    )
    assert module.validate(spec)["attempt"]["handoff_token"] == spec["attempt"]["id"]

@pytest.mark.parametrize("field,value", [("attempt", None), ("jdk", None)])
def test_rejects_token_or_jdk_mutation(field, value):
    spec=good()
    if field == "attempt": spec[field]["handoff_token"]="different-token-1234"
    else: spec[field]["source"]="/wrong/jdk"
    with pytest.raises(module.SpecError): module.validate(spec)

def test_rejects_stale_or_nondigest_image():
    spec=good(); spec["expires_at_utc"]=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with pytest.raises(module.SpecError): module.validate(spec)
    spec=good(); spec["images"]["client"]["image_id"]="latest"
    with pytest.raises(module.SpecError): module.validate(spec)
