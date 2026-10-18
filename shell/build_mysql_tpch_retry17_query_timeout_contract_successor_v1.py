#!/usr/bin/env python3
"""Build Retry17 with a 240s SQL limit inside the immutable 600s total cap."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry16", ROOT / "shell/build_mysql_tpch_retry16_native_stall_diagnostic_successor_v1.py"
)
retry16 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry16)
h = retry16.h

OLD_OUTER_ID = "a95fb87bdcd95c55d862f2d6ef540b5a5bbf934210df292376bec6b1731bd651"
OLD_OUTER_SHA = "edf37716756e991e212d3e9d05f142308f02c07c7adf6106440c6e216196d65c"
BASE = ROOT / "artifacts/mysql_tpch_retry16_native_stall_diagnostic_successor_v1" / OLD_OUTER_ID
OLD_TARGET_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"
OLD_TARGET_SHA = "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
OLD_RENDERER_ID = "a496ad36e202ed7ded0f4910fa16a9690bcc590d7d33de4f574fe7aba63e4226"
OLD_RENDERER_SHA = "f82f0d04d8f59b67d687f245874db6eeaf9c937d65e4add12464dbf0a3cf600a"
OLD_SUPPORT_ID = "4933006fc427393200bef05fdcbe55ca4c1568ea971a7db82afa68e08bb73a8d"
OLD_SUPPORT_SHA = "02dbabf7d6df20c416e9c96b71dfb0063dcb37442fbd65c442e256521bec56d4"
OLD_ENTRY_SHA = "5f1e27816dd725ec40c1c4711c5a67423db64362389a4f67e2d9a29fdc16be5a"
OLD_CORE_SHA = "fe02c5ac02824306d75f3c09c55fd75fd9d09dc96fc86255648cfcd1c32af559"
OLD_MAT_ID = "8b641b69b3222cd764860fa3f1fe6a3d86a8e9748d58720a3edc1fc29bd43f91"
OLD_MAT_SHA = "a3f68cace3cc64101cb0f358bda0b51753284f66c913f1dc6c79b97689f108a8"
OLD_WRAP_ID = "701e64c7b2102751bf3237d4ea51b7753f6518f2e6eba1d13e0a2e71e5a34aa7"
OLD_WRAP_SHA = "1a70bd7e12aa7809858093149c61e78ef495ca2735fb8475ef1cee23278e8b13"
RENDERER_OLD = BASE / "dependencies/bootstrap_renderer" / OLD_RENDERER_ID
TARGET_OLD = RENDERER_OLD / "target" / OLD_TARGET_ID
SUPPORT_OLD = BASE / "dependencies/support" / OLD_SUPPORT_ID
MAT_OLD = BASE / "dependencies/materializer" / OLD_MAT_ID
WRAP_OLD = BASE / "dependencies/wrapper" / OLD_WRAP_ID
ENTRY_REL = retry16.ENTRY_REL
CORE_REL = retry16.CORE_REL
CONTROLLER = retry16.CONTROLLER
RUNTIME = retry16.RUNTIME
ATTEMPT = "tpch-v2-20260803t233000p08-b235retry17timeout240"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
OUTPUT = RUNTIME / ("retry17_root_oneclick_" + ATTEMPT)

TARGET_OUT = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_target_successor_v1"
RENDERER_OUT = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_renderer_successor_v1"
SUPPORT_OUT = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_support_successor_v1"
MAT_OUT = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_materializer_successor_v1"
WRAP_OUT = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_wrapper_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_contract_successor_v1"


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


def build_target():
    old = h.verify(TARGET_OLD, OLD_TARGET_ID, OLD_TARGET_SHA)
    adapter_rel = "qualification/mysql_sql_debug_metric_adapter_v1.py"
    adapter = (TARGET_OLD / adapter_rel).read_text()
    adapter = h.once(
        adapter,
        '''# The collector's immutable graceful-exit gate is 120 seconds.  A query that
# starts immediately before the adapter deadline must therefore finish and let
# every worker join strictly inside that gate.  Ten seconds remain for Python,
# mysql-client and systemd scheduling after the 100-second server limit.
QUERY_EXECUTION_LIMIT_SECONDS = 100
QUERY_CLIENT_LIMIT_SECONDS = 110''',
        '''# The debug qualification requires 181 exact 1Hz samples while this SQL
# adapter has a frozen 240-second submission lifetime.  A 100-second server
# ceiling can therefore reject a legitimate in-flight TPC-H statement before
# the stable window closes.  The minimum contract-consistent server ceiling is
# 240 seconds; the client gets ten seconds to report that exact result.  The
# collector separately enforces a 600-second total qualification ceiling.
QUERY_EXECUTION_LIMIT_SECONDS = 240
QUERY_CLIENT_LIMIT_SECONDS = 250''',
        "adapter timeout constants",
    )
    adapter = h.once(
        adapter,
        "completes under the strict 100/110-second server/client ceilings;",
        "completes under the strict 240/250-second server/client ceilings;",
        "adapter timeout comment",
    )
    audit = {
        "schema_version": "mysql_tpch_retry17_query_timeout_contract_v1",
        "status": "SEALED_OFFLINE_NOT_EXECUTED",
        "observed_failed_attempt": retry16.FAILED,
        "observed_last_host_sequence": 100,
        "observed_last_native_sequence": 100,
        "observed_completed_total": 3,
        "observed_server_limit_sql": "SET SESSION MAX_EXECUTION_TIME=100000",
        "failing_query_name": None,
        "failing_query_name_reason": "not sealed in Retry15 stderr; do not infer or fabricate it",
        "adapter_duration_seconds": 240,
        "qualification_window_seconds": 180,
        "required_samples": 181,
        "sampling_hz": 1,
        "old_query_execution_limit_seconds": 100,
        "new_query_execution_limit_seconds": 240,
        "new_query_client_limit_seconds": 250,
        "collector_post_window_wait_seconds": 420,
        "total_qualification_upper_bound_seconds": 600,
        "failure_propagation_required": True,
        "synthetic_or_repeat_samples_prohibited": True,
        "resource_gates_relaxed": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
    }
    test = '''#!/usr/bin/env python3
import importlib.util,json,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"qualification/mysql_sql_debug_metric_adapter_v1.py"
q=importlib.util.spec_from_file_location("adapter",P);m=importlib.util.module_from_spec(q);q.loader.exec_module(m)
assert m.QUERY_EXECUTION_LIMIT_SECONDS==240
assert m.QUERY_CLIENT_LIMIT_SECONDS==250
assert m._effective_query_limit_seconds(600)==240
s=P.read_text();assert "MAX_EXECUTION_TIME=%d" in s and "metrics.fail" in s and "stop.set()" in s and "future.result()" in s
a=json.loads((B/"authority/mysql_tpch_retry17_query_timeout_contract_v1.json").read_text());assert a["required_samples"]==181 and a["sampling_hz"]==1 and a["resource_gates_relaxed"] is False and a["failing_query_name"] is None
print("PASS_RETRY17_TARGET_QUERY_TIMEOUT")
'''.encode()
    content = h.copied(TARGET_OLD, old, ("builder/", "tests/"))
    content[adapter_rel] = adapter.encode()
    content["authority/mysql_tpch_retry17_query_timeout_contract_v1.json"] = h.canonical(audit)
    content["tests/test_mysql_tpch_retry17_query_timeout_contract_v1.py"] = test
    content["builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry17_query_timeout_target_successor_bundle_v1",
                      "SEALED_RETRY17_TARGET_NOT_EXECUTED", OLD_TARGET_ID, OLD_TARGET_SHA)
    unsigned.update(
        query_execution_limit_seconds=240,
        query_client_limit_seconds=250,
        qualification_window_seconds=180,
        sql_adapter_duration_seconds=240,
        total_qualification_upper_bound_seconds=600,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        query_text_changed=False,
        weights_changed=False,
        concurrency_changed=False,
        failure_propagation_unchanged=True,
        synthetic_or_repeat_samples_prohibited=True,
        resource_gates_relaxed=False,
    )
    return h.publish(TARGET_OUT, unsigned, content, ".retry17-target-")


def build_renderer(target):
    old = h.verify(RENDERER_OLD, OLD_RENDERER_ID, OLD_RENDERER_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    target_collector_sha = h.sf(target / "qualification/mysql_distributed_live_readback_qualification_v1.py")
    rel = "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
    controller = (RENDERER_OLD / rel).read_text()
    for before, after, label in (
        (f'BUNDLE_ID = "{OLD_TARGET_ID}"', f'BUNDLE_ID = "{target.name}"', "target id"),
        (f'MANIFEST_SHA256 = "{OLD_TARGET_SHA}"', f'MANIFEST_SHA256 = "{target_sha}"', "target sha"),
        ('"89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"', f'"{target_collector_sha}"', "collector sha"),
    ):
        controller = h.once(controller, before, after, "renderer " + label)
    binding_rel = "b235_bootstrap_target_binding_v1.json"
    binding = json.loads((RENDERER_OLD / binding_rel).read_text())
    binding.update(
        schema_version="mysql_tpch_retry17_bootstrap_target_binding_v1",
        target_bundle_id=target.name,
        target_manifest_sha256=target_sha,
        target_collector_sha256=target_collector_sha,
        target_successor_generation="retry17_query_timeout_contract_v1",
    )
    content = h.copied(
        RENDERER_OLD, old,
        ("builder/", "offline_tests/", "target/" + OLD_TARGET_ID + "/"),
    )
    content[rel] = controller.encode()
    content[binding_rel] = h.canonical(binding)
    for path in sorted(target.rglob("*")):
        if path.is_file():
            content["target/" + target.name + "/" + path.relative_to(target).as_posix()] = path.read_bytes()
    test = f'''#!/usr/bin/env python3
import hashlib,json,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();T=B/"target/{target.name}";m=json.loads((T/"bundle_manifest.json").read_text());assert m["bundle_id"]==T.name=="{target.name}";assert hashlib.sha256((T/"bundle_manifest.json").read_bytes()).hexdigest()=="{target_sha}";b=json.loads((B/"{binding_rel}").read_text());assert b["target_bundle_id"]==T.name and b["target_manifest_sha256"]=="{target_sha}"
print("PASS_RETRY17_RENDERER")
'''.encode()
    content["offline_tests/test_mysql_tpch_retry17_renderer_v1.py"] = test
    content["builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry17_query_timeout_renderer_successor_bundle_v1",
                      "SEALED_RETRY17_RENDERER_NOT_EXECUTED", OLD_RENDERER_ID, OLD_RENDERER_SHA)
    unsigned.update(
        target_bundle_id=target.name,
        target_manifest_sha256=target_sha,
        target_collector_sha256=target_collector_sha,
        query_execution_limit_seconds=240,
        query_client_limit_seconds=250,
        total_qualification_upper_bound_seconds=600,
    )
    return h.publish(RENDERER_OUT, unsigned, content, ".retry17-renderer-")


def build_support():
    old = h.verify(SUPPORT_OLD, OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    core = (SUPPORT_OLD / CORE_REL).read_text()
    core = h.once(core, "query fails at the frozen 100-second server ceiling.",
                  "query fails at the frozen 240-second server ceiling.",
                  "Retry16 diagnostic comment")
    old_wait = '''                    # SQL runs for 240 seconds.  BenchBase receives the
                    # manifest-bound atomic 180-second stop request above.
                    # Both must exit naturally before immutable log hashing.
                    if stop_error is not None or primary_exception_active:
                        raise subprocess.TimeoutExpired(process.args, 0)
                    process.wait(timeout=120)'''
    new_wait = '''                    # SQL submission runs for 240 seconds.  After the
                    # exact 181-sample/180-second window, allow at most 420
                    # further seconds for a 240-second in-flight query and its
                    # 250-second client limit.  The qualification total remains
                    # capped at 600 seconds from the collector epoch.  Derive
                    # the remaining budget from the monotonic start so loop or
                    # scheduling overhead cannot extend that upper bound.
                    if stop_error is not None or primary_exception_active:
                        raise subprocess.TimeoutExpired(process.args, 0)
                    remaining_total_seconds = (
                        started + 600.0 - time.monotonic()
                    )
                    if remaining_total_seconds <= 0:
                        raise subprocess.TimeoutExpired(process.args, 600)
                    process.wait(timeout=remaining_total_seconds)'''
    core = h.once(core, old_wait, new_wait, "600-second total wait contract")
    content = h.copied(SUPPORT_OLD, old, ("builder/", "tests/"))
    content[CORE_REL] = core.encode()
    content["builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry17_query_timeout_support_v1.py"] = f'''#!/usr/bin/env python3
import pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();s=(B/"{CORE_REL}").read_text()
assert "started + 600.0 - time.monotonic()" in s
assert "process.wait(timeout=remaining_total_seconds)" in s
assert "frozen 240-second server ceiling" in s
assert "native workload metric producer stopped before" in s
assert "raise metric_error" in s
assert "MINIMUM_SAMPLES = 181" in s
assert "consume_exact_sequence=True" in s
assert "allow_fresh_repeat=True" in s
assert "timeout_threshold_relaxed" in s and "freshness_threshold_relaxed" in s
print("PASS_RETRY17_QUERY_TIMEOUT_SUPPORT")
'''.encode()
    unsigned = common(old, "mysql_tpch_retry17_query_timeout_support_successor_bundle_v1",
                      "SEALED_RETRY17_SUPPORT_NOT_EXECUTED", OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    unsigned.update(
        failed_retry15_native_stall_evidence_preserved=True,
        retry16_primary_error_propagation_preserved=True,
        collector_post_window_wait_seconds=420,
        total_qualification_upper_bound_seconds=600,
        minimum_samples_unchanged=181,
        exact_sequence_consumption_unchanged=True,
        metric_freshness_gate_unchanged=True,
        resource_gates_relaxed=False,
        synthetic_or_repeat_samples_prohibited=True,
    )
    return h.publish(SUPPORT_OUT, unsigned, content, ".retry17-support-")


def build_materializer(target, support):
    old = h.verify(MAT_OLD, OLD_MAT_ID, OLD_MAT_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (MAT_OLD / rel).read_text()
    source = h.once(source, f'TARGET_ID="{OLD_TARGET_ID}";TARGET_SHA="{OLD_TARGET_SHA}"',
                    f'TARGET_ID="{target.name}";TARGET_SHA="{target_sha}"', "materializer target")
    source = h.once(
        source,
        f'SUPPORT_ID="{OLD_SUPPORT_ID}";SUPPORT_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{OLD_CORE_SHA}"',
        f'SUPPORT_ID="{support.name}";SUPPORT_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{core_sha}"',
        "materializer support",
    )
    content = h.copied(MAT_OLD, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry17_query_timeout_materializer_successor_bundle_v1",
                      "SEALED_RETRY17_MATERIALIZER_NOT_EXECUTED", OLD_MAT_ID, OLD_MAT_SHA)
    unsigned.update(
        target_bundle_id=target.name, target_manifest_sha256=target_sha,
        support_bundle_id=support.name, support_manifest_sha256=support_sha,
        support_entry_sha256=entry_sha, support_core_sha256=core_sha,
        expected_sample_count=181, timeout_threshold_relaxed=False,
    )
    return h.publish(MAT_OUT, unsigned, content, ".retry17-mat-")


def build_wrapper(support, materializer):
    old = h.verify(WRAP_OLD, OLD_WRAP_ID, OLD_WRAP_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (WRAP_OLD / rel).read_text()
    source = h.once(source, f'RETRY_ID="{OLD_MAT_ID}";RETRY_SHA="{OLD_MAT_SHA}"',
                    f'RETRY_ID="{materializer.name}";RETRY_SHA="{materializer_sha}"', "wrapper materializer")
    source = h.once(
        source,
        f'COLLECTOR_ID="{OLD_SUPPORT_ID}";COLLECTOR_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{OLD_CORE_SHA}"',
        f'COLLECTOR_ID="{support.name}";COLLECTOR_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{core_sha}"',
        "wrapper support",
    )
    content = h.copied(WRAP_OLD, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry17_query_timeout_wrapper_successor_bundle_v1",
                      "SEALED_RETRY17_WRAPPER_NOT_EXECUTED", OLD_WRAP_ID, OLD_WRAP_SHA)
    unsigned.update(
        retry5_materializer_bundle_id=materializer.name,
        retry5_materializer_manifest_sha256=materializer_sha,
        collector_bundle_id=support.name,
        collector_manifest_sha256=support_sha,
        collector_entry_sha256=entry_sha,
        collector_core_sha256=core_sha,
    )
    return h.publish(WRAP_OUT, unsigned, content, ".retry17-wrap-")


def build_outer(target, renderer, support, materializer, wrapper):
    old = h.verify(BASE, OLD_OUTER_ID, OLD_OUTER_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    renderer_sha = h.sf(renderer / "bundle_manifest.json")
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    wrapper_sha = h.sf(wrapper / "bundle_manifest.json")
    source = (BASE / CONTROLLER).read_text()
    for before, after, label in (
        (f'TARGET_ID = "{OLD_TARGET_ID}"', f'TARGET_ID = "{target.name}"', "target id"),
        (f'TARGET_SHA = "{OLD_TARGET_SHA}"', f'TARGET_SHA = "{target_sha}"', "target sha"),
        (f'RENDERER_ID = "{OLD_RENDERER_ID}"', f'RENDERER_ID = "{renderer.name}"', "renderer id"),
        (f'RENDERER_SHA = "{OLD_RENDERER_SHA}"', f'RENDERER_SHA = "{renderer_sha}"', "renderer sha"),
        (f'SUPPORT_ID = "{OLD_SUPPORT_ID}"', f'SUPPORT_ID = "{support.name}"', "support id"),
        (f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"', f'SUPPORT_SHA = "{support_sha}"', "support sha"),
        (f'MATERIALIZER_ID = "{OLD_MAT_ID}"', f'MATERIALIZER_ID = "{materializer.name}"', "materializer id"),
        (f'MATERIALIZER_SHA = "{OLD_MAT_SHA}"', f'MATERIALIZER_SHA = "{materializer_sha}"', "materializer sha"),
        (f'WRAPPER_ID = "{OLD_WRAP_ID}"', f'WRAPPER_ID = "{wrapper.name}"', "wrapper id"),
        (f'WRAPPER_SHA = "{OLD_WRAP_SHA}"', f'WRAPPER_SHA = "{wrapper_sha}"', "wrapper sha"),
        (f'ROOT_SYSTEMD_ATTEMPT = "{retry16.ATTEMPT}"', f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"', "attempt"),
        (f'ROOT_SYSTEMD_UNIT = "{retry16.UNIT}"', f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "unit"),
        (f'ROOT_SYSTEMD_OUTPUT = Path("{retry16.OUTPUT}")', f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "output"),
    ):
        source = h.once(source, before, after, "outer " + label)
    skip = (
        "builder/", "tests/",
        "dependencies/bootstrap_renderer/" + OLD_RENDERER_ID + "/",
        "dependencies/support/" + OLD_SUPPORT_ID + "/",
        "dependencies/materializer/" + OLD_MAT_ID + "/",
        "dependencies/wrapper/" + OLD_WRAP_ID + "/",
    )
    content = h.copied(BASE, old, skip)
    content[CONTROLLER] = source.encode()
    for dependency, prefix in (
        (renderer, "dependencies/bootstrap_renderer/"),
        (support, "dependencies/support/"),
        (materializer, "dependencies/materializer/"),
        (wrapper, "dependencies/wrapper/"),
    ):
        for path in sorted(dependency.rglob("*")):
            if path.is_file():
                content[prefix + dependency.name + "/" + path.relative_to(dependency).as_posix()] = path.read_bytes()
    for rel in ("launch/root_start_and_wait_retry9_systemd.sh", "launch/run_retry9_root_systemd_principal.sh"):
        content[rel] = content[rel].decode().replace(retry16.ATTEMPT, ATTEMPT).replace(
            "retry16_root_oneclick_" + ATTEMPT, "retry17_root_oneclick_" + ATTEMPT).encode()
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(
        schema_version="mysql_tpch_retry17_query_timeout_node04_command_v1",
        attempt_token=ATTEMPT,
        systemd_unit=UNIT,
        target_bundle_id=target.name,
        renderer_bundle_id=renderer.name,
        support_closure_bundle_id=support.name,
        query_execution_limit_seconds=240,
        total_qualification_upper_bound_seconds=600,
    )
    content[template_rel] = h.canonical(template)
    content["tests/test_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = f'''#!/usr/bin/env python3
import importlib.util,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();T=B/"dependencies/bootstrap_renderer/{renderer.name}/target/{target.name}";S=B/"dependencies/support/{support.name}";C=B/"{CONTROLLER}"
subprocess.run([sys.executable,"-B",str(T/"tests/test_mysql_tpch_retry17_query_timeout_contract_v1.py"),str(T)],check=True)
subprocess.run([sys.executable,"-B",str(S/"tests/test_mysql_tpch_retry17_query_timeout_support_v1.py"),str(S)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package();assert p["renderer"].name=="{renderer.name}" and p["support"].name=="{support.name}";assert o.TARGET_ID=="{target.name}" and o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}"
print("PASS_RETRY17_QUERY_TIMEOUT_CONTRACT_SUCCESSOR")
'''.encode()
    content["builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry17_query_timeout_contract_successor_bundle_v1",
                      "SEALED_LOCAL_RETRY17_QUERY_TIMEOUT_NOT_EXECUTED", OLD_OUTER_ID, OLD_OUTER_SHA)
    unsigned.update(
        target_bundle_id=target.name, target_manifest_sha256=target_sha,
        renderer_bundle_id=renderer.name, renderer_manifest_sha256=renderer_sha,
        support_bundle_id=support.name, support_manifest_sha256=support_sha,
        support_entry_sha256=entry_sha, support_core_sha256=core_sha,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=materializer_sha,
        wrapper_bundle_id=wrapper.name, wrapper_manifest_sha256=wrapper_sha,
        new_attempt_token=ATTEMPT, root_systemd_unit=UNIT,
        retry16_real_error_evidence_preserved=True,
        query_execution_limit_seconds=240,
        query_client_limit_seconds=250,
        qualification_window_seconds=180,
        sql_adapter_duration_seconds=240,
        collector_post_window_wait_seconds=420,
        total_qualification_upper_bound_seconds=600,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        resource_gates_relaxed=False,
        synthetic_or_repeat_samples_prohibited=True,
        failure_propagation_required=True,
    )
    return h.publish(OUT, unsigned, content, ".retry17-outer-")


def main():
    target = build_target()
    renderer = build_renderer(target)
    support = build_support()
    materializer = build_materializer(target, support)
    wrapper = build_wrapper(support, materializer)
    outer = build_outer(target, renderer, support, materializer, wrapper)
    print(json.dumps({
        "bundle": str(outer), "bundle_id": outer.name,
        "manifest_sha256": h.sf(outer / "bundle_manifest.json"),
        "target_bundle_id": target.name,
        "target_manifest_sha256": h.sf(target / "bundle_manifest.json"),
        "renderer_bundle_id": renderer.name,
        "renderer_manifest_sha256": h.sf(renderer / "bundle_manifest.json"),
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
