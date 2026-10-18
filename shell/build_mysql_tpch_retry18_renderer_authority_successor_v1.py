#!/usr/bin/env python3
"""Build Retry18 after Retry17's renderer authority mismatch.

Offline only: this builder never publishes remotely, starts a unit, or mutates
the MySQL/PostgreSQL data plane.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry17", ROOT / "shell/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"
)
retry17 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry17)
h = retry17.h

OLD_OUTER_ID = "c26ff4afb61ef85c95211378b7428034aaa139186a7cd3ede5afba823976e221"
OLD_OUTER_SHA = "93e0d9ce532496f37894ad75262cd0e8ba7369a103e0ec04385930eb577785be"
OLD_OUTER = ROOT / "artifacts/mysql_tpch_retry17_query_timeout_contract_successor_v1" / OLD_OUTER_ID
TARGET_ID = "7128bc0dbd8f0ee0601b5cb19753b9d9ab147edc6fdf4107afc224108942ab78"
TARGET_SHA = "73c3f9eca83b004cfbc008c631fb3db88ae7c20a792b137fa75b0adf6677eeab"
TARGET_SCHEMA = "mysql_tpch_retry17_query_timeout_target_successor_bundle_v1"
TARGET_PREDECESSOR = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"
OLD_RENDERER_ID = "da36e78003aa1e21d5bda49e54ec4ff281111adf5fd87659e1c198ed693765b6"
OLD_RENDERER_SHA = "3efeb52949cc6a4437d6ca6f4726a4c9d3cf033fa598133824917497a7429aef"
OLD_RENDERER = OLD_OUTER / "dependencies/bootstrap_renderer" / OLD_RENDERER_ID
SUPPORT_ID = "ab465b86fb5b5b1906d512e8864c00e64fe2413a451c38e9649b4ef1116ad2d7"
MATERIALIZER_ID = "1a505c3d4f9717315c676784bbe67162acf8374a0fd3153c78bda4eed001304f"
WRAPPER_ID = "60c3178a199be0e1143e50d481fe2c670da68036c7f747c8d4c8be54883215e4"
OLD_ATTEMPT = "tpch-v2-20260803t233000p08-b235retry17timeout240"
OLD_UNIT = "ra-mysql-tpch-oneclick-root-" + OLD_ATTEMPT + ".service"
OLD_OUTPUT = retry17.RUNTIME / ("retry17_root_oneclick_" + OLD_ATTEMPT)
ATTEMPT = "tpch-v2-20260804t000500p08-b235retry18rendererfix"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
OUTPUT = retry17.RUNTIME / ("retry18_root_oneclick_" + ATTEMPT)
RENDERER_OUT = ROOT / "artifacts/mysql_tpch_retry18_renderer_authority_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry18_renderer_control_successor_v1"


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
        qualification_credit=0,
        r0_credit=0,
        remote_actions=0,
    )
    return value


def build_renderer():
    old = h.verify(OLD_RENDERER, OLD_RENDERER_ID, OLD_RENDERER_SHA)
    wrapper_rel = "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
    base_rel = "controller/render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3.py"
    base_spec_rel = "controller/render_mysql_tpch_v23_recalibrated_base_spec_v3.py"
    wrapper = (OLD_RENDERER / wrapper_rel).read_text(encoding="utf-8")
    wrapper = h.once(
        wrapper,
        '"predecessor_bundle_id":\n            "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4",',
        '"predecessor_bundle_id":\n            "' + TARGET_PREDECESSOR + '",\n'
        '        "target_schema_version":\n            "' + TARGET_SCHEMA + '",',
        "Retry18 actual predecessor and target schema binding",
    )
    wrapper = h.once(
        wrapper,
        '        "collector_sha256":\n            "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de",',
        '        "collector_sha256":\n            "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de",\n'
        '        "target_builder":\n            "builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py",',
        "Retry18 target builder binding",
    )
    wrapper = h.once(
        wrapper,
        "renderer.BUNDLE_ID = BUNDLE_ID",
        'renderer.SELF_CONTAINED_VERIFIER = (\n'
        '    "builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"\n'
        ')\nrenderer.BUNDLE_ID = BUNDLE_ID',
        "Retry18 self-contained target builder",
    )
    base = (OLD_RENDERER / base_rel).read_text(encoding="utf-8")
    base = h.once(
        base,
        'manifest.get("schema_version")\n        != "mysql_tpch_v23_recalibrated_successor_v3"',
        'manifest.get("schema_version")\n        != binding["target_schema_version"]',
        "binding-driven target schema verification",
    )
    legacy_builders = (
        '"builder/build_mysql_tpch_affinity_remediation_successor_v1.py"',
        '"builder/build_mysql_tpch_memswap_consistency_successor_v1.py"',
    )
    for legacy in legacy_builders:
        base = h.once(base, legacy, 'binding["target_builder"]',
                      "binding-driven target builder closure")
    base = h.once(
        base,
        "    base.DEFAULT_BUNDLE_MANIFEST = DEFAULT_BUNDLE_MANIFEST\n    document = base.render(args)",
        '    base.DEFAULT_BUNDLE_MANIFEST = DEFAULT_BUNDLE_MANIFEST\n'
        '    base.DEFAULT_BUNDLE_SCHEMA_VERSION = (\n'
        '        BINDINGS[BUNDLE_ID]["target_schema_version"]\n'
        '    )\n    document = base.render(args)',
        "binding-driven base renderer schema",
    )
    base_spec = (OLD_RENDERER / base_spec_rel).read_text(encoding="utf-8")
    base_spec = h.once(
        base_spec,
        'manifest.get("schema_version")\n        != "mysql_tpch_v23_recalibrated_successor_v3"',
        'manifest.get("schema_version")\n        != DEFAULT_BUNDLE_SCHEMA_VERSION',
        "base renderer target schema variable",
    )
    binding_rel = "b235_bootstrap_target_binding_v1.json"
    binding = json.loads((OLD_RENDERER / binding_rel).read_text(encoding="utf-8"))
    binding.update(
        schema_version="mysql_tpch_retry18_bootstrap_target_binding_v1",
        target_bundle_id=TARGET_ID,
        target_manifest_sha256=TARGET_SHA,
        target_predecessor_bundle_id=TARGET_PREDECESSOR,
        target_schema_version=TARGET_SCHEMA,
        target_builder="builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py",
        retry17_failed_attempt=OLD_ATTEMPT,
        retry17_rerun_authorized=False,
        accepted_rounds_credit=0,
    )
    content = h.copied(OLD_RENDERER, old, ("builder/", "offline_tests/"))
    content[wrapper_rel] = wrapper.encode("utf-8")
    content[base_rel] = base.encode("utf-8")
    content[base_spec_rel] = base_spec.encode("utf-8")
    content[binding_rel] = h.canonical(binding)
    regression = f'''#!/usr/bin/env python3
import hashlib,json,os,pathlib,subprocess,sys,tempfile
B=pathlib.Path(sys.argv[1]).resolve()
R=B/"{wrapper_rel}"
T=B/"target/{TARGET_ID}/bundle_manifest.json"
assert json.loads(T.read_text())["schema_version"]=="{TARGET_SCHEMA}"
assert json.loads(T.read_text())["predecessor_bundle_id"]=="{TARGET_PREDECESSOR}"
with tempfile.TemporaryDirectory(prefix="tpch-retry18-renderer-") as raw:
 d=pathlib.Path(raw);token=d/"main_agent.token";token.write_text("offline-renderer-regression-token\\n");token.chmod(0o600);out=d/"bootstrap_spec.json"
 cmd=[sys.executable,"-B",str(R),"--bundle-manifest",str(T),"--mysql-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--mysql-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--client-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--client-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--main-agent-token-file",str(token),"--attempt-token","tpch-v2-20260804t000000p08-b235retry18renderertest","--valid-minutes","60","--output",str(out)]
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
 assert p.returncode==0,(p.stdout,p.stderr)
 result=json.loads(p.stdout);spec=json.loads(out.read_text())
 assert result["status"]=="PASS_RENDERED_DEDICATED_DAEMON_QUALIFICATION_SPEC"
 assert result["bundle_id"]=="{TARGET_ID}"
 assert spec["formal_scope"]=="mysql_only" and spec["cross_engine_comparison_prohibited"] is True and spec["postgresql_mutation_prohibited"] is True
 assert spec["bundle"]["bundle_id"]=="{TARGET_ID}"
 assert spec["attempt_token"]=="tpch-v2-20260804t000000p08-b235retry18renderertest"
 assert pathlib.Path(str(out)+".sha256").read_text()==hashlib.sha256(out.read_bytes()).hexdigest()+"  bootstrap_spec.json\\n"
print("PASS_RETRY18_REAL_RENDERER_INVOCATION")
'''.encode("utf-8")
    content["offline_tests/test_mysql_tpch_retry18_real_renderer_invocation_v1.py"] = regression
    content["builder/build_mysql_tpch_retry18_renderer_authority_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(
        old, "mysql_tpch_retry18_renderer_authority_successor_bundle_v1",
        "SEALED_RETRY18_RENDERER_OFFLINE_NOT_EXECUTED", OLD_RENDERER_ID,
        OLD_RENDERER_SHA,
    )
    unsigned.update(
        target_bundle_id=TARGET_ID,
        target_manifest_sha256=TARGET_SHA,
        target_predecessor_bundle_id=TARGET_PREDECESSOR,
        target_schema_version=TARGET_SCHEMA,
        renderer_authority_schema_binding="binding_driven_exact_v1",
        real_renderer_invocation_regression=True,
        retry17_attempt_rerun_authorized=False,
        query_execution_limit_seconds=240,
        query_client_limit_seconds=250,
        qualification_window_seconds=180,
        minimum_samples=181,
        sampling_hz=1,
        total_qualification_upper_bound_seconds=600,
        resource_gates_relaxed=False,
    )
    return h.publish(RENDERER_OUT, unsigned, content, ".retry18-renderer-")


def build_outer(renderer: Path):
    old = h.verify(OLD_OUTER, OLD_OUTER_ID, OLD_OUTER_SHA)
    renderer_sha = h.sf(renderer / "bundle_manifest.json")
    controller_rel = retry17.CONTROLLER
    source = (OLD_OUTER / controller_rel).read_text(encoding="utf-8")
    for before, after, label in (
        (f'RENDERER_ID = "{OLD_RENDERER_ID}"', f'RENDERER_ID = "{renderer.name}"', "renderer id"),
        (f'RENDERER_SHA = "{OLD_RENDERER_SHA}"', f'RENDERER_SHA = "{renderer_sha}"', "renderer sha"),
        (f'ROOT_SYSTEMD_ATTEMPT = "{OLD_ATTEMPT}"', f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"', "attempt"),
        (f'ROOT_SYSTEMD_UNIT = "{OLD_UNIT}"', f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "unit"),
        (f'ROOT_SYSTEMD_OUTPUT = Path("{OLD_OUTPUT}")', f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "output"),
    ):
        source = h.once(source, before, after, "Retry18 outer " + label)
    source = h.once(
        source,
        f'FAILED_RETRY15_NATIVE_STALL_ATTEMPT = "{retry17.retry16.FAILED}"',
        f'FAILED_RETRY15_NATIVE_STALL_ATTEMPT = "{retry17.retry16.FAILED}"\n'
        f'FAILED_RETRY17_RENDERER_ATTEMPT = "{OLD_ATTEMPT}"',
        "Retry17 failed attempt constant",
    )
    source = h.once(
        source,
        '''    if attempt == FAILED_RETRY15_NATIVE_STALL_ATTEMPT:
        raise OneClickError("failed Retry15 native-stall attempt rerun is prohibited")
    target = runtime_root / "bundles" / TARGET_ID''',
        '''    if attempt == FAILED_RETRY15_NATIVE_STALL_ATTEMPT:
        raise OneClickError("failed Retry15 native-stall attempt rerun is prohibited")
    if attempt == FAILED_RETRY17_RENDERER_ATTEMPT:
        raise OneClickError("failed Retry17 renderer-authority attempt rerun is prohibited")
    target = runtime_root / "bundles" / TARGET_ID''',
        "Retry17 rerun prohibition",
    )
    skip = (
        "builder/", "tests/",
        "dependencies/bootstrap_renderer/" + OLD_RENDERER_ID + "/",
    )
    content = h.copied(OLD_OUTER, old, skip)
    content[controller_rel] = source.encode("utf-8")
    for path in sorted(renderer.rglob("*")):
        if path.is_file():
            content["dependencies/bootstrap_renderer/" + renderer.name + "/" + path.relative_to(renderer).as_posix()] = path.read_bytes()
    for rel in (
        "launch/root_start_and_wait_retry9_systemd.sh",
        "launch/run_retry9_root_systemd_principal.sh",
    ):
        text = content[rel].decode("utf-8")
        text = text.replace(OLD_ATTEMPT, ATTEMPT)
        text = text.replace("retry17_root_oneclick_", "retry18_root_oneclick_")
        content[rel] = text.encode("utf-8")
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(
        schema_version="mysql_tpch_retry18_renderer_authority_node04_command_v1",
        attempt_token=ATTEMPT,
        systemd_unit=UNIT,
        target_bundle_id=TARGET_ID,
        renderer_bundle_id=renderer.name,
        query_execution_limit_seconds=240,
        query_client_limit_seconds=250,
        qualification_window_seconds=180,
        required_samples=181,
        sampling_hz=1,
        total_qualification_upper_bound_seconds=600,
        retry17_rerun_authorized=False,
    )
    content[template_rel] = h.canonical(template)
    failure = {
        "schema_version":"mysql_tpch_retry17_renderer_authority_failure_v1",
        "status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token":OLD_ATTEMPT,"systemd_unit":OLD_UNIT,
        "observed_at":"2026-08-03T23:20:13+08:00",
        "exec_main_pid":27854,"exec_main_status":2,
        "fresh_error":"bootstrap spec renderer failed rc=2 stderr=NOT_READY: systemd-compatible successor authority differs",
        "bootstrap_spec_created":False,"controller_pid":None,
        "copy_pid":None,"workload_pid":None,"sequence_started":False,
        "root_cause":{"bound_predecessor":"ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4",
          "actual_predecessor":TARGET_PREDECESSOR,
          "bound_schema":"mysql_tpch_v23_recalibrated_successor_v3",
          "actual_schema":TARGET_SCHEMA},
        "rerun_authorized":False,"formal_scope":"mysql_only",
        "cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True,"accepted_rounds_credit":0,
    }
    failure_rel = "evidence/mysql_tpch_retry17_renderer_authority_failure_v1.json"
    content[failure_rel] = h.canonical(failure)
    content[failure_rel + ".sha256"] = (
        h.sb(content[failure_rel]) + "  " + Path(failure_rel).name + "\n"
    ).encode("ascii")
    test_rel = "tests/test_mysql_tpch_retry18_renderer_control_successor_v1.py"
    content[test_rel] = f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();R=B/"dependencies/bootstrap_renderer/{renderer.name}";T=R/"target/{TARGET_ID}";S=B/"dependencies/support/{SUPPORT_ID}";C=B/"{controller_rel}"
subprocess.run([sys.executable,"-B",str(T/"tests/test_mysql_tpch_retry17_query_timeout_contract_v1.py"),str(T)],check=True)
subprocess.run([sys.executable,"-B",str(S/"tests/test_mysql_tpch_retry17_query_timeout_support_v1.py"),str(S)],check=True)
subprocess.run([sys.executable,"-B",str(R/"offline_tests/test_mysql_tpch_retry18_real_renderer_invocation_v1.py"),str(R)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package()
assert p["renderer"].name=="{renderer.name}" and o.TARGET_ID=="{TARGET_ID}"
assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and o.ROOT_SYSTEMD_UNIT=="{UNIT}"
assert o.FAILED_RETRY17_RENDERER_ATTEMPT=="{OLD_ATTEMPT}"
f=json.loads((B/"{failure_rel}").read_text());assert f["rerun_authorized"] is False and f["accepted_rounds_credit"]==0 and f["controller_pid"] is None and f["workload_pid"] is None
t=json.loads((B/"{template_rel}").read_text());assert t["query_execution_limit_seconds"]==240 and t["query_client_limit_seconds"]==250 and t["required_samples"]==181 and t["sampling_hz"]==1 and t["total_qualification_upper_bound_seconds"]==600
print("PASS_RETRY18_RENDERER_CONTROL_SUCCESSOR")
'''.encode("utf-8")
    content["builder/build_mysql_tpch_retry18_renderer_authority_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(
        old, "mysql_tpch_retry18_renderer_control_successor_bundle_v1",
        "SEALED_LOCAL_RETRY18_RENDERER_CONTROL_NOT_EXECUTED",
        OLD_OUTER_ID, OLD_OUTER_SHA,
    )
    unsigned.update(
        target_bundle_id=TARGET_ID,
        target_manifest_sha256=TARGET_SHA,
        target_predecessor_bundle_id=TARGET_PREDECESSOR,
        target_schema_version=TARGET_SCHEMA,
        renderer_bundle_id=renderer.name,
        renderer_manifest_sha256=renderer_sha,
        support_bundle_id=SUPPORT_ID,
        materializer_bundle_id=MATERIALIZER_ID,
        wrapper_bundle_id=WRAPPER_ID,
        failed_retry17_attempt=OLD_ATTEMPT,
        failed_retry17_rerun_authorized=False,
        new_attempt_token=ATTEMPT,
        root_systemd_unit=UNIT,
        real_renderer_invocation_regression=True,
        query_execution_limit_seconds=240,
        query_client_limit_seconds=250,
        qualification_window_seconds=180,
        sql_adapter_duration_seconds=240,
        collector_post_window_wait_seconds=420,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        total_qualification_upper_bound_seconds=600,
        resource_gates_relaxed=False,
        synthetic_or_repeat_samples_prohibited=True,
        failure_propagation_required=True,
    )
    return h.publish(OUT, unsigned, content, ".retry18-outer-")


def main():
    renderer = build_renderer()
    outer = build_outer(renderer)
    print(json.dumps({
        "bundle":str(outer),"bundle_id":outer.name,
        "manifest_sha256":h.sf(outer/"bundle_manifest.json"),
        "renderer_bundle":str(renderer),"renderer_bundle_id":renderer.name,
        "renderer_manifest_sha256":h.sf(renderer/"bundle_manifest.json"),
        "target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,
        "attempt_token":ATTEMPT,"systemd_unit":UNIT,
    },sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
        raise SystemExit("NOT_READY: "+str(exc))
