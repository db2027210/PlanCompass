#!/usr/bin/env python3
"""Build Retry21 with the final target schema accepted by its real collector."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry20", ROOT / "shell/build_mysql_tpch_retry20_current_target_seal_successor_v1.py"
)
r20 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r20)
h = r20.h

OLD_TARGET_ID = "5a9cb02fb6eb136200d8efb838d1e967c7f93ad444f7c3bed594b4bd787d0f8c"
OLD_TARGET_SHA = "b0544dc6fb11b6891d8c4f310fa550bda92d2ee21a9d42d7b4821f2f4ffbbd4a"
OLD_RENDERER_ID = "a8863de52af367727c1a268260cb80176f6cbd93bf52bd7de41062c1e6028c39"
OLD_RENDERER_SHA = "750f31602fc441358ed95d1bdaea332e1a1a3b1ee28dad45bf1cf4df06448e98"
OLD_MATERIALIZER_ID = "df8631691c45b23bf5eff0ed65d86f27fc04b73b22c96d2ad38ca1600c842d9c"
OLD_MATERIALIZER_SHA = "0f901f8ab61ac5231a8a055bb29a577fd0476801ee29d042f9f24c4a1e89c022"
OLD_WRAPPER_ID = "d6eb6d3c2b1b011941781f5552ab51d94941dff5434892d7a67276195afc8dea"
OLD_WRAPPER_SHA = "7073a69e57a7fe81e84fc0a6c0e2bfda8598b395582ee254fb7166142a2ee2bb"
OLD_OUTER_ID = "b52db36cb6529c430524c80a539d75c95a7b5c0ff984dde5ecb4041e5a2cd3e9"
OLD_OUTER_SHA = "358ee2176b4179c8e2d7d763227453f84f7b2d3d827ef32461932033b4d45685"

OLD_TARGET = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_target_successor_v1" / OLD_TARGET_ID
OLD_RENDERER = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_renderer_successor_v1" / OLD_RENDERER_ID
OLD_MATERIALIZER = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_materializer_successor_v1" / OLD_MATERIALIZER_ID
OLD_WRAPPER = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_wrapper_successor_v1" / OLD_WRAPPER_ID
OLD_OUTER = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_control_successor_v1" / OLD_OUTER_ID

OLD_ATTEMPT = "tpch-v2-20260804t160000p08-b235retry20targetseal"
OLD_UNIT = "ra-mysql-tpch-oneclick-root-" + OLD_ATTEMPT + ".service"
ATTEMPT = "tpch-v2-20260804t161000p08-b235retry21collectorschema"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OLD_OUTPUT = RUNTIME / ("retry20_root_oneclick_" + OLD_ATTEMPT)
OUTPUT = RUNTIME / ("retry21_root_oneclick_" + ATTEMPT)

TARGET_SCHEMA = "mysql_tpch_retry21_collector_schema_successor_bundle_v1"
BUILDER_REL = "builder/build_mysql_tpch_retry21_collector_schema_successor_v1.py"
TARGET_OUT = ROOT / "artifacts/mysql_tpch_retry21_collector_schema_target_successor_v1"
RENDERER_OUT = ROOT / "artifacts/mysql_tpch_retry21_collector_schema_renderer_successor_v1"
MATERIALIZER_OUT = ROOT / "artifacts/mysql_tpch_retry21_collector_schema_materializer_successor_v1"
WRAPPER_OUT = ROOT / "artifacts/mysql_tpch_retry21_collector_schema_wrapper_successor_v1"
OUTER_OUT = ROOT / "artifacts/mysql_tpch_retry21_collector_schema_control_successor_v1"


def common(old, schema, status, predecessor_id, predecessor_sha):
    value = {k: v for k, v in old.items() if k not in (
        "bundle_id", "files", "schema_version", "status",
        "predecessor_bundle_id", "predecessor_manifest_sha256",
    )}
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


def load_collector(path):
    module_spec = importlib.util.spec_from_file_location("retry21_collector", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def build_target():
    old = h.verify(OLD_TARGET, OLD_TARGET_ID, OLD_TARGET_SHA)
    rel = "qualification/mysql_distributed_live_readback_qualification_v1.py"
    source = (OLD_TARGET / rel).read_text()
    source = h.once(
        source,
        '        "mysql_tpch_retry19_collector_schema_target_successor_bundle_v1",\n    ):',
        '        "mysql_tpch_retry19_collector_schema_target_successor_bundle_v1",\n'
        f'        "{TARGET_SCHEMA}",\n    ):',
        "Retry21 collector final schema allow-list",
    )
    content = h.copied(OLD_TARGET, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["authority/mysql_tpch_retry20_prepublication_collector_schema_failure_v1.json"] = h.canonical({
        "schema_version": "mysql_tpch_retry20_prepublication_collector_schema_failure_v1",
        "status": "FAILED_PREPUBLICATION_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token": OLD_ATTEMPT,
        "failure_stage": "remote_exact_self_verify_before_publish",
        "fresh_error": "distributed bundle schema differs",
        "published": False,
        "database_or_client_started": False,
        "controller_pid": None,
        "workload_pid": None,
        "rerun_authorized": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "r0_credit": 0,
    })
    content["tests/test_mysql_tpch_retry21_final_collector_verify_v1.py"] = f'''#!/usr/bin/env python3
import importlib.util,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"{rel}"
q=importlib.util.spec_from_file_location("final_collector",P);m=importlib.util.module_from_spec(q);q.loader.exec_module(m)
m.verify_bundle(B)
print("PASS_RETRY21_FINAL_COLLECTOR_VERIFY")
'''.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, TARGET_SCHEMA,
                      "SEALED_RETRY21_COLLECTOR_SCHEMA_OFFLINE_NOT_EXECUTED",
                      OLD_TARGET_ID, OLD_TARGET_SHA)
    hashes = dict(unsigned.get("component_hashes") or {})
    hashes["collector"] = h.sb(content[rel])
    unsigned.update(
        component_hashes=hashes,
        live_readback_collector_schema_binding=TARGET_SCHEMA,
        final_target_real_collector_verify_required=True,
        failed_retry20_attempt=OLD_ATTEMPT,
        failed_retry20_rerun_authorized=False,
        resource_gates_relaxed=False,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        total_qualification_upper_bound_seconds=600,
    )
    target = h.publish(TARGET_OUT, unsigned, content, ".retry21-target-")
    load_collector(target / rel).verify_bundle(target)
    return target


def build_renderer(target):
    old = h.verify(OLD_RENDERER, OLD_RENDERER_ID, OLD_RENDERER_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    collector_sha = h.sf(target / "qualification/mysql_distributed_live_readback_qualification_v1.py")
    rel = "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
    source = (OLD_RENDERER / rel).read_text()
    for before, after, label in (
        (f'BUNDLE_ID = "{OLD_TARGET_ID}"', f'BUNDLE_ID = "{target.name}"', "target id"),
        (f'MANIFEST_SHA256 = "{OLD_TARGET_SHA}"', f'MANIFEST_SHA256 = "{target_sha}"', "target sha"),
        ('"76455cbd09944d5bc2f150e731a820899922b6b60069517e11cc6325fb60f917"', f'"{OLD_TARGET_ID}"', "predecessor"),
        ('"mysql_tpch_retry20_current_target_seal_successor_bundle_v1"', f'"{TARGET_SCHEMA}"', "schema"),
        ('"8957b60b9e8d279e0ec41d302a8332d174c1a4f001a4bb5e709c156ffe8c3b20"', f'"{collector_sha}"', "collector"),
        ('"target_builder":\n            "builder/build_mysql_tpch_retry20_current_target_seal_successor_v1.py"',
         f'"target_builder":\n            "{BUILDER_REL}"', "target builder"),
    ):
        source = h.once(source, before, after, "Retry21 renderer " + label)
    source = h.once(
        source,
        'renderer.SELF_CONTAINED_VERIFIER = (\n    "builder/build_mysql_tpch_retry20_current_target_seal_successor_v1.py"\n)',
        f'renderer.SELF_CONTAINED_VERIFIER = (\n    "{BUILDER_REL}"\n)',
        "Retry21 renderer self verifier",
    )
    binding_rel = "b235_bootstrap_target_binding_v1.json"
    binding = json.loads((OLD_RENDERER / binding_rel).read_text())
    binding.update(
        schema_version="mysql_tpch_retry21_collector_schema_binding_v1",
        target_bundle_id=target.name,
        target_manifest_sha256=target_sha,
        target_predecessor_bundle_id=OLD_TARGET_ID,
        target_schema_version=TARGET_SCHEMA,
        target_collector_sha256=collector_sha,
        target_builder=BUILDER_REL,
        retry20_failed_attempt=OLD_ATTEMPT,
        retry20_rerun_authorized=False,
        accepted_rounds_credit=0,
    )
    content = h.copied(OLD_RENDERER, old, ("builder/", "offline_tests/", "target/" + OLD_TARGET_ID + "/"))
    content[rel] = source.encode()
    content[binding_rel] = h.canonical(binding)
    for path in sorted(target.rglob("*")):
        if path.is_file():
            content["target/" + target.name + "/" + path.relative_to(target).as_posix()] = path.read_bytes()
    content["offline_tests/test_mysql_tpch_retry21_real_renderer_v1.py"] = f'''#!/usr/bin/env python3
import json,pathlib,subprocess,sys,tempfile
B=pathlib.Path(sys.argv[1]).resolve();R=B/"{rel}";T=B/"target/{target.name}/bundle_manifest.json"
with tempfile.TemporaryDirectory(prefix="tpch-retry21-renderer-") as raw:
 d=pathlib.Path(raw);token=d/"main_agent.token";token.write_text("offline-retry21-token\\n");token.chmod(0o600);out=d/"spec.json"
 cmd=[sys.executable,"-B",str(R),"--bundle-manifest",str(T),"--mysql-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--mysql-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--client-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--client-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--main-agent-token-file",str(token),"--attempt-token","tpch-v2-20260804t160500p08-b235retry21renderertest","--valid-minutes","60","--output",str(out)]
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30);assert p.returncode==0,(p.stdout,p.stderr)
 s=json.loads(out.read_text());assert s["bundle"]["bundle_id"]=="{target.name}" and s["formal_scope"]=="mysql_only"
print("PASS_RETRY21_REAL_RENDERER")
'''.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry21_collector_schema_renderer_successor_bundle_v1",
                      "SEALED_RETRY21_RENDERER_OFFLINE_NOT_EXECUTED",
                      OLD_RENDERER_ID, OLD_RENDERER_SHA)
    unsigned.update(target_bundle_id=target.name, target_manifest_sha256=target_sha,
                    target_predecessor_bundle_id=OLD_TARGET_ID,
                    target_schema_version=TARGET_SCHEMA,
                    target_collector_sha256=collector_sha,
                    real_final_target_collector_verify_required=True,
                    failed_retry20_rerun_authorized=False,
                    resource_gates_relaxed=False)
    return h.publish(RENDERER_OUT, unsigned, content, ".retry21-renderer-")


def build_materializer(target):
    old = h.verify(OLD_MATERIALIZER, OLD_MATERIALIZER_ID, OLD_MATERIALIZER_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (OLD_MATERIALIZER / rel).read_text()
    source = h.once(source,
                    f'TARGET_ID="{OLD_TARGET_ID}";TARGET_SHA="{OLD_TARGET_SHA}"',
                    f'TARGET_ID="{target.name}";TARGET_SHA="{target_sha}"',
                    "Retry21 materializer target")
    content = h.copied(OLD_MATERIALIZER, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry21_collector_schema_materializer_successor_bundle_v1",
                      "SEALED_RETRY21_MATERIALIZER_OFFLINE_NOT_EXECUTED",
                      OLD_MATERIALIZER_ID, OLD_MATERIALIZER_SHA)
    unsigned.update(target_bundle_id=target.name, target_manifest_sha256=target_sha,
                    expected_sample_count=181, timeout_threshold_relaxed=False)
    return h.publish(MATERIALIZER_OUT, unsigned, content, ".retry21-materializer-")


def build_wrapper(materializer):
    old = h.verify(OLD_WRAPPER, OLD_WRAPPER_ID, OLD_WRAPPER_SHA)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (OLD_WRAPPER / rel).read_text()
    source = h.once(source,
                    f'RETRY_ID="{OLD_MATERIALIZER_ID}";RETRY_SHA="{OLD_MATERIALIZER_SHA}"',
                    f'RETRY_ID="{materializer.name}";RETRY_SHA="{materializer_sha}"',
                    "Retry21 wrapper materializer")
    content = h.copied(OLD_WRAPPER, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry21_collector_schema_wrapper_successor_bundle_v1",
                      "SEALED_RETRY21_WRAPPER_OFFLINE_NOT_EXECUTED",
                      OLD_WRAPPER_ID, OLD_WRAPPER_SHA)
    unsigned.update(retry5_materializer_bundle_id=materializer.name,
                    retry5_materializer_manifest_sha256=materializer_sha)
    return h.publish(WRAPPER_OUT, unsigned, content, ".retry21-wrapper-")


def build_outer(target, renderer, materializer, wrapper):
    old = h.verify(OLD_OUTER, OLD_OUTER_ID, OLD_OUTER_SHA)
    tsha = h.sf(target / "bundle_manifest.json")
    rsha = h.sf(renderer / "bundle_manifest.json")
    msha = h.sf(materializer / "bundle_manifest.json")
    wsha = h.sf(wrapper / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry9_one_click_v1.py"
    source = (OLD_OUTER / rel).read_text()
    for before, after, label in (
        (f'TARGET_ID = "{OLD_TARGET_ID}"', f'TARGET_ID = "{target.name}"', "target id"),
        (f'TARGET_SHA = "{OLD_TARGET_SHA}"', f'TARGET_SHA = "{tsha}"', "target sha"),
        (f'RENDERER_ID = "{OLD_RENDERER_ID}"', f'RENDERER_ID = "{renderer.name}"', "renderer id"),
        (f'RENDERER_SHA = "{OLD_RENDERER_SHA}"', f'RENDERER_SHA = "{rsha}"', "renderer sha"),
        (f'MATERIALIZER_ID = "{OLD_MATERIALIZER_ID}"', f'MATERIALIZER_ID = "{materializer.name}"', "materializer id"),
        (f'MATERIALIZER_SHA = "{OLD_MATERIALIZER_SHA}"', f'MATERIALIZER_SHA = "{msha}"', "materializer sha"),
        (f'WRAPPER_ID = "{OLD_WRAPPER_ID}"', f'WRAPPER_ID = "{wrapper.name}"', "wrapper id"),
        (f'WRAPPER_SHA = "{OLD_WRAPPER_SHA}"', f'WRAPPER_SHA = "{wsha}"', "wrapper sha"),
        (f'ROOT_SYSTEMD_ATTEMPT = "{OLD_ATTEMPT}"', f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"', "attempt"),
        (f'ROOT_SYSTEMD_UNIT = "{OLD_UNIT}"', f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "unit"),
        (f'ROOT_SYSTEMD_OUTPUT = Path("{OLD_OUTPUT}")', f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "output"),
    ):
        source = h.once(source, before, after, "Retry21 outer " + label)
    source = h.once(
        source,
        f'FAILED_RETRY19_TARGET_SEAL_ATTEMPT = "{r20.OLD_ATTEMPT}"',
        f'FAILED_RETRY19_TARGET_SEAL_ATTEMPT = "{r20.OLD_ATTEMPT}"\nFAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT = "{OLD_ATTEMPT}"',
        "Retry20 failed constant",
    )
    source = h.once(
        source,
        '    if attempt == FAILED_RETRY19_TARGET_SEAL_ATTEMPT:\n'
        '        raise OneClickError("failed Retry19 current-target-seal attempt rerun is prohibited")\n'
        '    target = runtime_root / "bundles" / TARGET_ID',
        '    if attempt == FAILED_RETRY19_TARGET_SEAL_ATTEMPT:\n'
        '        raise OneClickError("failed Retry19 current-target-seal attempt rerun is prohibited")\n'
        '    if attempt == FAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT:\n'
        '        raise OneClickError("failed Retry20 prepublication collector-schema attempt rerun is prohibited")\n'
        '    target = runtime_root / "bundles" / TARGET_ID',
        "Retry20 rerun prohibition",
    )
    skip = (
        "builder/", "tests/",
        "dependencies/bootstrap_renderer/" + OLD_RENDERER_ID + "/",
        "dependencies/materializer/" + OLD_MATERIALIZER_ID + "/",
        "dependencies/wrapper/" + OLD_WRAPPER_ID + "/",
    )
    content = h.copied(OLD_OUTER, old, skip)
    content[rel] = source.encode()
    for dependency, prefix in (
        (renderer, "dependencies/bootstrap_renderer/"),
        (materializer, "dependencies/materializer/"),
        (wrapper, "dependencies/wrapper/"),
    ):
        for path in sorted(dependency.rglob("*")):
            if path.is_file():
                content[prefix + dependency.name + "/" + path.relative_to(dependency).as_posix()] = path.read_bytes()
    for launch_rel in (
        "launch/root_start_and_wait_retry9_systemd.sh",
        "launch/run_retry9_root_systemd_principal.sh",
    ):
        content[launch_rel] = content[launch_rel].decode().replace(
            OLD_ATTEMPT, ATTEMPT
        ).replace("retry20_root_oneclick_", "retry21_root_oneclick_").encode()
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(
        schema_version="mysql_tpch_retry21_collector_schema_node04_command_v1",
        attempt_token=ATTEMPT,
        systemd_unit=UNIT,
        target_bundle_id=target.name,
        renderer_bundle_id=renderer.name,
        retry20_rerun_authorized=False,
    )
    content[template_rel] = h.canonical(template)
    content["evidence/mysql_tpch_retry20_prepublication_collector_schema_failure_v1.json"] = h.canonical({
        "schema_version": "mysql_tpch_retry20_prepublication_collector_schema_failure_v1",
        "status": "FAILED_PREPUBLICATION_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token": OLD_ATTEMPT,
        "failure_stage": "remote_exact_self_verify_before_publish",
        "fresh_error": "distributed bundle schema differs",
        "published": False,
        "database_or_client_started": False,
        "controller_pid": None,
        "workload_pid": None,
        "rerun_authorized": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "r0_credit": 0,
    })
    test_rel = "tests/test_mysql_tpch_retry21_collector_schema_control_v1.py"
    content[test_rel] = f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();R=B/"dependencies/bootstrap_renderer/{renderer.name}";T=R/"target/{target.name}";C=B/"{rel}"
P=T/"qualification/mysql_distributed_live_readback_qualification_v1.py";q=importlib.util.spec_from_file_location("final_collector",P);collector=importlib.util.module_from_spec(q);q.loader.exec_module(collector);collector.verify_bundle(T)
subprocess.run([sys.executable,"-B",str(R/"offline_tests/test_mysql_tpch_retry21_real_renderer_v1.py"),str(R)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package()
assert p["renderer"].name=="{renderer.name}" and o.TARGET_ID=="{target.name}" and o.TARGET_SHA=="{tsha}"
assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and o.FAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT=="{OLD_ATTEMPT}"
m=json.loads((T/"bundle_manifest.json").read_text());assert m["schema_version"]=="{TARGET_SCHEMA}" and m["live_readback_collector_schema_binding"]=="{TARGET_SCHEMA}"
assert m["component_hashes"]["collector"]=="{h.sf(target / "qualification/mysql_distributed_live_readback_qualification_v1.py")}" and m["files"]["qualification/mysql_distributed_live_readback_qualification_v1.py"]==m["component_hashes"]["collector"]
outer=json.loads((B/"bundle_manifest.json").read_text());assert outer["accepted_rounds_credit"]==0 and outer["failed_retry20_rerun_authorized"] is False
print("PASS_RETRY21_COLLECTOR_SCHEMA_CONTROL")
'''.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry21_collector_schema_control_successor_bundle_v1",
                      "SEALED_LOCAL_RETRY21_COLLECTOR_SCHEMA_NOT_EXECUTED",
                      OLD_OUTER_ID, OLD_OUTER_SHA)
    unsigned.update(
        target_bundle_id=target.name,
        target_manifest_sha256=tsha,
        renderer_bundle_id=renderer.name,
        renderer_manifest_sha256=rsha,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=msha,
        wrapper_bundle_id=wrapper.name,
        wrapper_manifest_sha256=wsha,
        failed_retry20_attempt=OLD_ATTEMPT,
        failed_retry20_rerun_authorized=False,
        retry20_published=False,
        new_attempt_token=ATTEMPT,
        root_systemd_unit=UNIT,
        final_target_real_collector_verify_required=True,
        final_target_real_collector_verify_passed=True,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        total_qualification_upper_bound_seconds=600,
        resource_gates_relaxed=False,
        synthetic_or_repeat_samples_prohibited=True,
        failure_propagation_required=True,
    )
    return h.publish(OUTER_OUT, unsigned, content, ".retry21-outer-")


def main():
    target = build_target()
    renderer = build_renderer(target)
    materializer = build_materializer(target)
    wrapper = build_wrapper(materializer)
    outer = build_outer(target, renderer, materializer, wrapper)
    print(json.dumps({
        "bundle": str(outer),
        "bundle_id": outer.name,
        "manifest_sha256": h.sf(outer / "bundle_manifest.json"),
        "target_bundle_id": target.name,
        "target_manifest_sha256": h.sf(target / "bundle_manifest.json"),
        "target_collector_sha256": h.sf(target / "qualification/mysql_distributed_live_readback_qualification_v1.py"),
        "renderer_bundle_id": renderer.name,
        "renderer_manifest_sha256": h.sf(renderer / "bundle_manifest.json"),
        "materializer_bundle_id": materializer.name,
        "materializer_manifest_sha256": h.sf(materializer / "bundle_manifest.json"),
        "wrapper_bundle_id": wrapper.name,
        "wrapper_manifest_sha256": h.sf(wrapper / "bundle_manifest.json"),
        "attempt_token": ATTEMPT,
        "systemd_unit": UNIT,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
