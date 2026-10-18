#!/usr/bin/env python3
"""Build Retry16 preserving the native SQL failure behind a metric stall."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry15_builder", ROOT / "shell/build_mysql_tpch_retry15_exact_process_identity_successor_v1.py"
)
retry15 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry15)
h = retry15.h

OLD_OUTER_ID = "d561f9359574996317d520741856186fbb57798367549a3fad4e1fb92f7aa351"
OLD_OUTER_SHA = "4bd979c259e77165c73ec0bddfbd7c703568b77fa911270b76918361ce15467a"
BASE = ROOT / "artifacts/mysql_tpch_retry15_exact_process_identity_successor_v1" / OLD_OUTER_ID
OLD_SUPPORT_ID = "6406ccc0a35152b978454f1b12e4c6732dd976b43d0167c1970cb5c5b0f53b98"
OLD_SUPPORT_SHA = "689e5d782b8fae7466d9c3f7b2c469ecbff56b3a822338ea457832f6493452d7"
OLD_ENTRY_SHA = "5f1e27816dd725ec40c1c4711c5a67423db64362389a4f67e2d9a29fdc16be5a"
OLD_CORE_SHA = "00d731034a55758fe0947b8a15954e66b97921e748adbc3b09c4966878eb2d85"
OLD_MAT_ID = "540ba5de4372c148eaacfd0ad93463dd85be042177ed11196c1bc63a905c6f2f"
OLD_MAT_SHA = "ebe85b35755f6f0b71c01550b006b0dd27151ce03ff7246082aa030a4c6dba1f"
OLD_WRAP_ID = "9cdc39c302bf42b65285f699cf22a45b9fbfbb8f8908e3af9ac2a8de35ccef69"
OLD_WRAP_SHA = "889b9280ec9419b2644f314dd55ecf2a27c86bec275e41dcf9824e9c9fb4d6bf"
SUPPORT_OLD = BASE / "dependencies/support" / OLD_SUPPORT_ID
MAT_OLD = BASE / "dependencies/materializer" / OLD_MAT_ID
WRAP_OLD = BASE / "dependencies/wrapper" / OLD_WRAP_ID
ENTRY_REL = retry15.ENTRY_REL
CORE_REL = retry15.CORE_REL
CONTROLLER = retry15.CONTROLLER
RUNTIME = retry15.RUNTIME
FAILED = "tpch-v2-20260803t221500p08-b235retry15procid4e2a"
ATTEMPT = "tpch-v2-20260803t224500p08-b235retry16stall8a4d"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
OUTPUT = RUNTIME / ("retry16_root_oneclick_" + ATTEMPT)
FAILURE = "evidence/mysql_tpch_retry15_native_metric_stall_failure_v1.json"
SUPPORT_OUT = ROOT / "artifacts/mysql_tpch_retry16_native_stall_diagnostic_support_successor_v1"
MAT_OUT = ROOT / "artifacts/mysql_tpch_retry16_native_stall_diagnostic_materializer_successor_v1"
WRAP_OUT = ROOT / "artifacts/mysql_tpch_retry16_native_stall_diagnostic_wrapper_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry16_native_stall_diagnostic_successor_v1"


def common(old, schema, status, predecessor_id, predecessor_sha):
    value = {
        key: item for key, item in old.items()
        if key not in ("bundle_id", "files", "schema_version", "status",
                       "predecessor_bundle_id", "predecessor_manifest_sha256")
    }
    value.update(
        schema_version=schema,
        status=status,
        predecessor_bundle_id=predecessor_id,
        predecessor_manifest_sha256=predecessor_sha,
        formal_scope="mysql_only",
        cross_engine_comparison_prohibited=True,
        postgresql_mutation_prohibited=True,
        formal_launch_authorized=False,
        accepted_round_authorized=False,
        accepted_rounds_credit=0,
        remote_actions=0,
    )
    return value


def build_support():
    old = h.verify(SUPPORT_OLD, OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    core = (SUPPORT_OLD / CORE_REL).read_text()
    old_call = '''                dynamic_sample = collect_dynamic_sample(
                    index,
                    sampled,
                    started,
                    own,
                    target_pid,
                    client_pid,
                    process.pid,
                    spec["workload_process_isolation"],
                    mysql_binary,
                    defaults_file,
                    metric_path,
                    spec["qualification_attempt_id"],
                    previous_sequence,
                )
'''
    new_call = '''                try:
                    dynamic_sample = collect_dynamic_sample(
                        index,
                        sampled,
                        started,
                        own,
                        target_pid,
                        client_pid,
                        process.pid,
                        spec["workload_process_isolation"],
                        mysql_binary,
                        defaults_file,
                        metric_path,
                        spec["qualification_attempt_id"],
                        previous_sequence,
                    )
                except QualificationError as metric_error:
                    # The SQL adapter can stop its source stream when a real
                    # query fails at the frozen 100-second server ceiling.
                    # Give the already-running native producer only enough
                    # time to publish its fail-closed diagnostic, then retain
                    # that primary cause instead of a secondary missing-suffix
                    # symptom.  No sample is repeated or synthesized.
                    diagnostic_deadline = time.monotonic() + 1.5
                    while (native_metric_producer_state["error"] is None
                           and process.poll() is None
                           and time.monotonic() < diagnostic_deadline):
                        time.sleep(0.02)
                    producer_error = native_metric_producer_state["error"]
                    if producer_error is not None:
                        raise QualificationError(
                            "native workload metric producer stopped before "
                            "required sequence %d: %s"
                            % (index, producer_error)
                        )
                    workload_rc = process.poll()
                    if workload_rc is not None:
                        stderr_tail = ""
                        try:
                            stderr_tail = stderr_path.read_text(
                                encoding="utf-8", errors="replace"
                            )[-1000:].strip()
                        except OSError:
                            pass
                        raise QualificationError(
                            "native SQL workload exited before required "
                            "sequence %d rc=%d stderr=%s"
                            % (index, workload_rc, stderr_tail)
                        )
                    raise metric_error
'''
    core = h.once(core, old_call, new_call, "native stall primary diagnostic")
    content = h.copied(SUPPORT_OLD, old, ("builder/", "tests/"))
    content[CORE_REL] = core.encode()
    content["builder/build_mysql_tpch_retry16_native_stall_diagnostic_successor_v1.py"] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry16_native_stall_diagnostic_v1.py"] = f'''#!/usr/bin/env python3
import pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"{CORE_REL}";s=P.read_text()
assert 'native workload metric producer stopped before' in s
assert 'diagnostic_deadline = time.monotonic() + 1.5' in s
assert 'raise metric_error' in s
assert 'allow_fresh_repeat=True' in s
assert 'consume_exact_sequence=True' in s
assert 'MINIMUM_SAMPLES = 181' in s
assert 'database_or_benchmark_process_identity' in s
print("PASS_RETRY16_NATIVE_STALL_DIAGNOSTIC")
'''.encode()
    unsigned = common(old, "mysql_tpch_retry16_native_stall_diagnostic_support_successor_bundle_v1",
                      "SEALED_NATIVE_STALL_DIAGNOSTIC_SUPPORT_NOT_EXECUTED",
                      OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    unsigned.update(
        failed_retry15_attempt_token=FAILED,
        failed_retry15_rerun_prohibited=True,
        minimum_samples_unchanged=181,
        exact_sequence_consumption_unchanged=True,
        metric_freshness_gate_unchanged=True,
        synthetic_or_repeat_samples_prohibited=True,
        native_stall_primary_cause_preserved=True,
    )
    return h.publish(SUPPORT_OUT, unsigned, content, ".retry16-support-")


def build_materializer(support):
    old = h.verify(MAT_OLD, OLD_MAT_ID, OLD_MAT_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (MAT_OLD / rel).read_text()
    source = h.once(
        source,
        f'SUPPORT_ID="{OLD_SUPPORT_ID}";SUPPORT_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{OLD_CORE_SHA}"',
        f'SUPPORT_ID="{support.name}";SUPPORT_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{core_sha}"',
        "materializer support closure",
    )
    content = h.copied(MAT_OLD, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry16_native_stall_diagnostic_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry16_native_stall_diagnostic_materializer_successor_bundle_v1",
                      "SEALED_RETRY16_MATERIALIZER_NOT_EXECUTED", OLD_MAT_ID, OLD_MAT_SHA)
    unsigned.update(support_bundle_id=support.name, support_manifest_sha256=support_sha,
                    support_entry_sha256=entry_sha, support_core_sha256=core_sha)
    return h.publish(MAT_OUT, unsigned, content, ".retry16-mat-")


def build_wrapper(support, materializer):
    old = h.verify(WRAP_OLD, OLD_WRAP_ID, OLD_WRAP_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (WRAP_OLD / rel).read_text()
    source = h.once(source, f'RETRY_ID="{OLD_MAT_ID}";RETRY_SHA="{OLD_MAT_SHA}"',
                    f'RETRY_ID="{materializer.name}";RETRY_SHA="{materializer_sha}"',
                    "wrapper materializer closure")
    source = h.once(
        source,
        f'COLLECTOR_ID="{OLD_SUPPORT_ID}";COLLECTOR_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{OLD_CORE_SHA}"',
        f'COLLECTOR_ID="{support.name}";COLLECTOR_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{core_sha}"',
        "wrapper support closure",
    )
    content = h.copied(WRAP_OLD, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry16_native_stall_diagnostic_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry16_native_stall_diagnostic_wrapper_successor_bundle_v1",
                      "SEALED_RETRY16_WRAPPER_NOT_EXECUTED", OLD_WRAP_ID, OLD_WRAP_SHA)
    unsigned.update(
        retry5_materializer_bundle_id=materializer.name,
        retry5_materializer_manifest_sha256=materializer_sha,
        collector_bundle_id=support.name,
        collector_manifest_sha256=support_sha,
        collector_entry_sha256=entry_sha,
        collector_core_sha256=core_sha,
    )
    return h.publish(WRAP_OUT, unsigned, content, ".retry16-wrap-")


def build_outer(support, materializer, wrapper):
    old = h.verify(BASE, OLD_OUTER_ID, OLD_OUTER_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    wrapper_sha = h.sf(wrapper / "bundle_manifest.json")
    source = (BASE / CONTROLLER).read_text()
    replacements = (
        (f'SUPPORT_ID = "{OLD_SUPPORT_ID}"', f'SUPPORT_ID = "{support.name}"', "support id"),
        (f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"', f'SUPPORT_SHA = "{support_sha}"', "support sha"),
        (f'MATERIALIZER_ID = "{OLD_MAT_ID}"', f'MATERIALIZER_ID = "{materializer.name}"', "materializer id"),
        (f'MATERIALIZER_SHA = "{OLD_MAT_SHA}"', f'MATERIALIZER_SHA = "{materializer_sha}"', "materializer sha"),
        (f'WRAPPER_ID = "{OLD_WRAP_ID}"', f'WRAPPER_ID = "{wrapper.name}"', "wrapper id"),
        (f'WRAPPER_SHA = "{OLD_WRAP_SHA}"', f'WRAPPER_SHA = "{wrapper_sha}"', "wrapper sha"),
    )
    for before, after, label in replacements:
        source = h.once(source, before, after, "outer " + label)
    source = h.once(source,
        f'FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT = "tpch-v2-20260803t220000p08-b235retry14argv2d6e9"\nROOT_SYSTEMD_ATTEMPT = "{FAILED}"',
        f'FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT = "tpch-v2-20260803t220000p08-b235retry14argv2d6e9"\nFAILED_RETRY15_NATIVE_STALL_ATTEMPT = "{FAILED}"\nROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"',
        "Retry16 attempt")
    source = h.once(source, f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED}.service"',
                    f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "Retry16 unit")
    source = h.once(source, f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry15_root_oneclick_" + FAILED)}")',
                    f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "Retry16 output")
    source = h.once(source,
        '        "failure_retry14_process_identity": root / "evidence/mysql_tpch_retry14_process_identity_false_positive_failure_v1.json",\n',
        '        "failure_retry14_process_identity": root / "evidence/mysql_tpch_retry14_process_identity_false_positive_failure_v1.json",\n'
        f'        "failure_retry15_native_stall": root / "{FAILURE}",\n',
        "Retry15 failure path")
    source = h.once(source,
        '        raise OneClickError("Retry14 process identity exact zero-credit evidence differs")\n    return paths\n',
        '''        raise OneClickError("Retry14 process identity exact zero-credit evidence differs")
    retry15 = sealed(paths["failure_retry15_native_stall"], "Retry15 native metric stall failure")
    if (retry15.get("schema_version") != "mysql_tpch_retry15_native_metric_stall_failure_v1" or retry15.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN" or retry15.get("attempt_token") != FAILED_RETRY15_NATIVE_STALL_ATTEMPT or retry15.get("fresh_error") != "retry5 launch failed rc=2 stderr=NOT_READY: workload metric sample missing/stale/timed out: workload metric contiguous suffix is absent" or retry15.get("host_metric_sequences") != {"first": 0, "last": 100, "count": 101} or retry15.get("native_metric_sequences") != {"first": 0, "last": 100, "count": 101} or retry15.get("process_inventory_conflicts") != [] or retry15.get("current_pids") != {"bootstrap": None, "controller": None, "collector": None, "sql_workload": None} or retry15.get("post_reconcile_passed") is not True or retry15.get("post_closure_passed") is not True or retry15.get("rerun_prohibited") is not True or retry15.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry15 native metric stall exact zero-credit evidence differs")
    return paths
''', "Retry15 failure verify")
    source = h.once(source,
        '    if attempt == FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT:\n        raise OneClickError("failed Retry14 process-identity attempt rerun is prohibited")\n',
        '    if attempt == FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT:\n        raise OneClickError("failed Retry14 process-identity attempt rerun is prohibited")\n'
        '    if attempt == FAILED_RETRY15_NATIVE_STALL_ATTEMPT:\n        raise OneClickError("failed Retry15 native-stall attempt rerun is prohibited")\n',
        "Retry15 rerun guard")
    source = h.once(source,
        '        "failed_retry14_process_identity_evidence": str(paths["failure_retry14_process_identity"]),\n',
        '        "failed_retry14_process_identity_evidence": str(paths["failure_retry14_process_identity"]),\n'
        '        "failed_retry15_native_stall_attempt": FAILED_RETRY15_NATIVE_STALL_ATTEMPT,\n'
        '        "failed_retry15_native_stall_evidence": str(paths["failure_retry15_native_stall"]),\n',
        "Retry15 plan evidence")
    source = h.once(source,
        '            or plan.get("attempt_token") == FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',
        '            or plan.get("attempt_token") == FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT\n'
        '            or plan.get("failed_retry15_native_stall_attempt") != FAILED_RETRY15_NATIVE_STALL_ATTEMPT\n'
        '            or plan.get("attempt_token") == FAILED_RETRY15_NATIVE_STALL_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n',
        "Retry15 execute guard")
    skip = ("builder/", "tests/", f"dependencies/support/{OLD_SUPPORT_ID}/",
            f"dependencies/materializer/{OLD_MAT_ID}/", f"dependencies/wrapper/{OLD_WRAP_ID}/")
    content = h.copied(BASE, old, skip)
    content[CONTROLLER] = source.encode()
    for dependency, prefix in ((support, "dependencies/support/"),
                               (materializer, "dependencies/materializer/"),
                               (wrapper, "dependencies/wrapper/")):
        for path in sorted(dependency.rglob("*")):
            if path.is_file():
                content[prefix + dependency.name + "/" + path.relative_to(dependency).as_posix()] = path.read_bytes()
    failure = {
        "schema_version": "mysql_tpch_retry15_native_metric_stall_failure_v1",
        "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": OLD_OUTER_ID,
        "predecessor_manifest_sha256": OLD_OUTER_SHA,
        "attempt_token": FAILED,
        "unit": "ra-mysql-tpch-oneclick-root-" + FAILED + ".service",
        "historical_pids": {"root_controller": 94066, "bootstrap": 94114, "controller": 106314, "collector": 106316, "sql_workload": 106708},
        "current_pids": {"bootstrap": None, "controller": None, "collector": None, "sql_workload": None},
        "host_metric_sequences": {"first": 0, "last": 100, "count": 101},
        "native_metric_sequences": {"first": 0, "last": 100, "count": 101},
        "workload_stderr_bytes": 0,
        "completed_total_at_sequence_100": 3,
        "process_inventory_conflicts": [],
        "process_inventory_snapshot_consistent": True,
        "process_inventory_complete": True,
        "permission_failure_count": 0,
        "race_count": 0,
        "foreign_writer_count": 0,
        "fresh_error": "retry5 launch failed rc=2 stderr=NOT_READY: workload metric sample missing/stale/timed out: workload metric contiguous suffix is absent",
        "root_cause": "SQL native adapter worker stops the shared emitter after a real query reaches the frozen 100-second execution ceiling; collector index 101 then reports the secondary absent-suffix symptom before the native failure becomes terminal",
        "inner_failure_sha256": "e935d06f6a49f5175005977541043476cedd56d635d0de104784789ad68bddf9",
        "reconcile_sha256": "480817eba13504577ebf2525429e2ef41214e3031c81d3e032a734742dbea3d9",
        "post_reconcile_passed": True,
        "post_closure_passed": True,
        "rerun_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    raw = h.canonical(failure)
    content[FAILURE] = raw
    content[FAILURE + ".sha256"] = (h.sb(raw) + "  " + Path(FAILURE).name + "\n").encode()
    for rel in ("launch/root_start_and_wait_retry9_systemd.sh", "launch/run_retry9_root_systemd_principal.sh"):
        content[rel] = content[rel].decode().replace(FAILED, ATTEMPT).replace(
            "retry15_root_oneclick_" + ATTEMPT, "retry16_root_oneclick_" + ATTEMPT).encode()
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(schema_version="mysql_tpch_retry16_native_stall_diagnostic_node04_command_v1",
                    attempt_token=ATTEMPT, systemd_unit=UNIT,
                    support_closure_bundle_id=support.name,
                    failed_retry15_attempt_rerun_prohibited=True)
    content[template_rel] = h.canonical(template)
    content["tests/test_mysql_tpch_retry16_native_stall_diagnostic_successor_v1.py"] = f'''#!/usr/bin/env python3
import importlib.util,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();S=B/"dependencies/support/{support.name}";M=B/"dependencies/materializer/{materializer.name}";W=B/"dependencies/wrapper/{wrapper.name}";C=B/"{CONTROLLER}"
subprocess.run([sys.executable,"-B",str(S/"tests/test_mysql_tpch_retry16_native_stall_diagnostic_v1.py"),str(S)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package();assert p["support"]==S and p["materializer"]==M and p["wrapper"]==W;assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}"
print("PASS_RETRY16_NATIVE_STALL_DIAGNOSTIC_SUCCESSOR")
'''.encode()
    content["builder/build_mysql_tpch_retry16_native_stall_diagnostic_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry16_native_stall_diagnostic_successor_bundle_v1",
                      "SEALED_LOCAL_RETRY16_NATIVE_STALL_DIAGNOSTIC_NOT_EXECUTED",
                      OLD_OUTER_ID, OLD_OUTER_SHA)
    unsigned.update(
        support_bundle_id=support.name,
        support_manifest_sha256=support_sha,
        support_entry_sha256=entry_sha,
        support_core_sha256=core_sha,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=materializer_sha,
        wrapper_bundle_id=wrapper.name,
        wrapper_manifest_sha256=wrapper_sha,
        failed_retry15_attempt_token=FAILED,
        failed_retry15_rerun_prohibited=True,
        new_attempt_token=ATTEMPT,
        root_systemd_unit=UNIT,
        native_stall_primary_cause_preserved=True,
        minimum_samples_unchanged=181,
        synthetic_or_repeat_samples_prohibited=True,
    )
    return h.publish(OUT, unsigned, content, ".retry16-outer-")


def main():
    support = build_support()
    materializer = build_materializer(support)
    wrapper = build_wrapper(support, materializer)
    outer = build_outer(support, materializer, wrapper)
    print(json.dumps({
        "bundle": str(outer),
        "bundle_id": outer.name,
        "manifest_sha256": h.sf(outer / "bundle_manifest.json"),
        "support_bundle_id": support.name,
        "support_manifest_sha256": h.sf(support / "bundle_manifest.json"),
        "support_entry_sha256": h.sf(support / ENTRY_REL),
        "support_core_sha256": h.sf(support / CORE_REL),
        "materializer_bundle_id": materializer.name,
        "materializer_manifest_sha256": h.sf(materializer / "bundle_manifest.json"),
        "wrapper_bundle_id": wrapper.name,
        "wrapper_manifest_sha256": h.sf(wrapper / "bundle_manifest.json"),
        "attempt_token": ATTEMPT,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
