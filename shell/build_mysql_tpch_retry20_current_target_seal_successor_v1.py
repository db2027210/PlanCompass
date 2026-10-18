#!/usr/bin/env python3
"""Build Retry20 after Retry19 used a stale target seal in the blkio supplement."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry19", ROOT / "shell/build_mysql_tpch_retry19_collector_schema_successor_v1.py"
)
r19 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r19)
h = r19.h

OLD_TARGET_ID = "76455cbd09944d5bc2f150e731a820899922b6b60069517e11cc6325fb60f917"
OLD_TARGET_SHA = "b7729f6221751ee48b715a3b60b786373e96e4764e6418046913582e8a3a6c6c"
OLD_RENDERER_ID = "bb51c538c955e18890d929011de0fbae2f84005148fb3095e47bd8e5a8d9ac20"
OLD_RENDERER_SHA = "626e4e2478bf50a34d6f0d9d27b200ca30c0564824348e4cd245651b0516f23d"
OLD_MATERIALIZER_ID = "7403a7e8b536438b1325b9949d5adbcac6f8a7be21172be39ae408f662b70ef4"
OLD_MATERIALIZER_SHA = "fe0649b0cef5194ec8716bd7c634d143c8d446e2151d7462458d3ce93538989e"
OLD_WRAPPER_ID = "b467a8b11c29f45ebdb4788ef51018b80bb9e113ef9c005c801c384db5784fd5"
OLD_WRAPPER_SHA = "283ff898472ab7e8f375ccde1e26b26dc3dffa1cce6eed020c357fb0fa0cb942"
OLD_OUTER_ID = "347c091e4529017f78e34e505cea2b027652b69825f5a517394d7154a3905f31"
OLD_OUTER_SHA = "00231793c1b7f081400a19c0b07d489483847da16dd738909a7095b736a6e466"

OLD_TARGET = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_target_successor_v1" / OLD_TARGET_ID
OLD_RENDERER = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_renderer_successor_v1" / OLD_RENDERER_ID
OLD_MATERIALIZER = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_materializer_successor_v1" / OLD_MATERIALIZER_ID
OLD_WRAPPER = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_wrapper_successor_v1" / OLD_WRAPPER_ID
OLD_OUTER = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_control_successor_v1" / OLD_OUTER_ID
SUPPORT_ID = "ab465b86fb5b5b1906d512e8864c00e64fe2413a451c38e9649b4ef1116ad2d7"
SUPPORT_SHA = "53bddeb7076d7e1225ca9945191f609023ea04e45909098503c4005d437a5550"
SUPPORT = OLD_OUTER / "dependencies/support" / SUPPORT_ID

OLD_ATTEMPT = "tpch-v2-20260804t153000p08-b235retry19collectschema"
OLD_UNIT = "ra-mysql-tpch-oneclick-root-" + OLD_ATTEMPT + ".service"
ATTEMPT = "tpch-v2-20260804t160000p08-b235retry20targetseal"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OLD_OUTPUT = RUNTIME / ("retry19_root_oneclick_" + OLD_ATTEMPT)
OUTPUT = RUNTIME / ("retry20_root_oneclick_" + ATTEMPT)

TARGET_SCHEMA = "mysql_tpch_retry20_current_target_seal_successor_bundle_v1"
BUILDER_REL = "builder/build_mysql_tpch_retry20_current_target_seal_successor_v1.py"
SUPPLEMENT_REL = "qualification/mysql_tpch_current_target_blkio_supplement_v2.py"
TARGET_OUT = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_target_successor_v1"
RENDERER_OUT = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_renderer_successor_v1"
MATERIALIZER_OUT = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_materializer_successor_v1"
WRAPPER_OUT = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_wrapper_successor_v1"
OUTER_OUT = ROOT / "artifacts/mysql_tpch_retry20_current_target_seal_control_successor_v1"


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


def build_target():
    old = h.verify(OLD_TARGET, OLD_TARGET_ID, OLD_TARGET_SHA)
    content = h.copied(OLD_TARGET, old, ("builder/", "tests/"))
    source = (SUPPORT / "qualification/mysql_tpch_legacy_blkio_supplement_v1.py").read_text()
    source = h.once(
        source,
        'TARGET_BUNDLE_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"\n'
        'TARGET_MANIFEST_SHA256 = "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"',
        'HEX64 = re.compile(r"[0-9a-f]{64}$")',
        "remove stale target constants",
    )
    source = h.once(
        source,
        "def build(bundle, authority_path, container_name):\n"
        "    bundle = Path(bundle).resolve(strict=True)\n"
        "    if (bundle.name != TARGET_BUNDLE_ID\n"
        "            or sha256(bundle / \"bundle_manifest.json\") != TARGET_MANIFEST_SHA256):\n"
        "        raise SupplementError(\"exact current-target target seal differs\")",
        "def build(bundle, authority_path, container_name, expected_target_id, expected_target_sha256):\n"
        "    if (HEX64.fullmatch(expected_target_id or \"\") is None\n"
        "            or HEX64.fullmatch(expected_target_sha256 or \"\") is None):\n"
        "        raise SupplementError(\"sealed expected current-target identity differs\")\n"
        "    bundle = Path(bundle).resolve(strict=True)\n"
        "    if (bundle.name != expected_target_id\n"
        "            or sha256(bundle / \"bundle_manifest.json\") != expected_target_sha256):\n"
        "        raise SupplementError(\"exact current-target target seal differs\")",
        "bind target seal from caller",
    )
    source = h.once(source, 'or authority.get("bundle_id") != TARGET_BUNDLE_ID',
                    'or authority.get("bundle_id") != expected_target_id',
                    "authority target identity")
    source = h.once(source, '"bundle_id": TARGET_BUNDLE_ID,\n        "bundle_manifest_sha256": TARGET_MANIFEST_SHA256,',
                    '"bundle_id": expected_target_id,\n        "bundle_manifest_sha256": expected_target_sha256,',
                    "result target identity")
    source = h.once(
        source,
        '    parser.add_argument("--container", required=True)\n'
        '    parser.add_argument("--output", type=Path, required=True)',
        '    parser.add_argument("--container", required=True)\n'
        '    parser.add_argument("--expected-target-id", required=True)\n'
        '    parser.add_argument("--expected-target-manifest-sha256", required=True)\n'
        '    parser.add_argument("--output", type=Path, required=True)',
        "sealed target CLI",
    )
    source = h.once(
        source,
        "    value = build(args.bundle, args.legacy_authority, args.container)",
        "    value = build(args.bundle, args.legacy_authority, args.container,\n"
        "                  args.expected_target_id, args.expected_target_manifest_sha256)",
        "sealed target call",
    )
    content[SUPPLEMENT_REL] = source.encode()
    failure = {
        "schema_version": "mysql_tpch_retry19_current_target_seal_failure_v1",
        "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token": OLD_ATTEMPT,
        "systemd_unit": OLD_UNIT,
        "failed_at_local": "2026-08-04T15:44:59+08:00",
        "failure_stage": "fresh_blkio_supplement",
        "fresh_error": "fresh blkio supplement failed rc=2 stderr=NOT_READY: exact current-target target seal differs",
        "bootstrap_succeeded": True,
        "database_pid_at_start": 95262,
        "client_pid_at_start": 95578,
        "controller_pid_current": None,
        "workload_pid_current": None,
        "cleanup_confirmation": "PENDING_MAIN_AGENT_REMOTE_READ_ONLY_CLOSURE",
        "rerun_authorized": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "r0_credit": 0,
    }
    content["authority/mysql_tpch_retry19_current_target_seal_failure_v1.json"] = h.canonical(failure)
    content["tests/test_mysql_tpch_retry20_target_seal_successor_v1.py"] = b'''#!/usr/bin/env python3
import importlib.util,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"qualification/mysql_tpch_current_target_blkio_supplement_v2.py"
q=importlib.util.spec_from_file_location("supplement",P);m=importlib.util.module_from_spec(q);q.loader.exec_module(m)
s=P.read_text();assert "TARGET_BUNDLE_ID =" not in s and "TARGET_MANIFEST_SHA256 =" not in s
assert "expected_target_id" in s and "expected_target_sha256" in s
print("PASS_RETRY20_TARGET_SEAL_SUCCESSOR")
'''
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, TARGET_SCHEMA,
                      "SEALED_RETRY20_TARGET_SEAL_OFFLINE_NOT_EXECUTED",
                      OLD_TARGET_ID, OLD_TARGET_SHA)
    hashes = dict(unsigned.get("component_hashes") or {})
    hashes["current_target_blkio_supplement_v2"] = h.sb(content[SUPPLEMENT_REL])
    unsigned.update(
        component_hashes=hashes,
        current_target_supplement_relpath=SUPPLEMENT_REL,
        current_target_identity_source="sealed_outer_exact_argv",
        stale_support_target_identity_prohibited=True,
        failed_retry19_attempt=OLD_ATTEMPT,
        failed_retry19_rerun_authorized=False,
        retry19_cleanup_confirmation_required_before_launch=True,
        resource_gates_relaxed=False,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        total_qualification_upper_bound_seconds=600,
    )
    return h.publish(TARGET_OUT, unsigned, content, ".retry20-target-")


def build_renderer(target):
    old = h.verify(OLD_RENDERER, OLD_RENDERER_ID, OLD_RENDERER_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    collector_sha = h.sf(target / "qualification/mysql_distributed_live_readback_qualification_v1.py")
    rel = "controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
    source = (OLD_RENDERER / rel).read_text()
    for before, after, label in (
        (f'BUNDLE_ID = "{OLD_TARGET_ID}"', f'BUNDLE_ID = "{target.name}"', "target id"),
        (f'MANIFEST_SHA256 = "{OLD_TARGET_SHA}"', f'MANIFEST_SHA256 = "{target_sha}"', "target sha"),
        ('"7128bc0dbd8f0ee0601b5cb19753b9d9ab147edc6fdf4107afc224108942ab78"', f'"{OLD_TARGET_ID}"', "predecessor"),
        ('"mysql_tpch_retry19_collector_schema_target_successor_bundle_v1"', f'"{TARGET_SCHEMA}"', "schema"),
        ('"8957b60b9e8d279e0ec41d302a8332d174c1a4f001a4bb5e709c156ffe8c3b20"', f'"{collector_sha}"', "collector"),
        ('"target_builder":\n            "builder/build_mysql_tpch_retry19_collector_schema_successor_v1.py"',
         f'"target_builder":\n            "{BUILDER_REL}"', "target builder"),
    ):
        source = h.once(source, before, after, "Retry20 renderer " + label)
    source = h.once(
        source,
        'renderer.SELF_CONTAINED_VERIFIER = (\n    "builder/build_mysql_tpch_retry19_collector_schema_successor_v1.py"\n)',
        f'renderer.SELF_CONTAINED_VERIFIER = (\n    "{BUILDER_REL}"\n)',
        "Retry20 renderer self-contained builder",
    )
    binding_rel = "b235_bootstrap_target_binding_v1.json"
    binding = json.loads((OLD_RENDERER / binding_rel).read_text())
    binding.update(
        schema_version="mysql_tpch_retry20_current_target_seal_binding_v1",
        target_bundle_id=target.name,
        target_manifest_sha256=target_sha,
        target_predecessor_bundle_id=OLD_TARGET_ID,
        target_schema_version=TARGET_SCHEMA,
        target_collector_sha256=collector_sha,
        target_builder=BUILDER_REL,
        retry19_failed_attempt=OLD_ATTEMPT,
        retry19_rerun_authorized=False,
        accepted_rounds_credit=0,
    )
    content = h.copied(OLD_RENDERER, old, ("builder/", "offline_tests/", "target/" + OLD_TARGET_ID + "/"))
    content[rel] = source.encode()
    content[binding_rel] = h.canonical(binding)
    for path in sorted(target.rglob("*")):
        if path.is_file():
            content["target/" + target.name + "/" + path.relative_to(target).as_posix()] = path.read_bytes()
    content["offline_tests/test_mysql_tpch_retry20_real_renderer_v1.py"] = f'''#!/usr/bin/env python3
import json,pathlib,subprocess,sys,tempfile
B=pathlib.Path(sys.argv[1]).resolve();R=B/"{rel}";T=B/"target/{target.name}/bundle_manifest.json"
with tempfile.TemporaryDirectory(prefix="tpch-retry20-renderer-") as raw:
 d=pathlib.Path(raw);token=d/"main_agent.token";token.write_text("offline-retry20-token\\n");token.chmod(0o600);out=d/"spec.json"
 cmd=[sys.executable,"-B",str(R),"--bundle-manifest",str(T),"--mysql-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--mysql-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--client-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--client-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--main-agent-token-file",str(token),"--attempt-token","tpch-v2-20260804t155500p08-b235retry20renderertest","--valid-minutes","60","--output",str(out)]
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30);assert p.returncode==0,(p.stdout,p.stderr)
 s=json.loads(out.read_text());assert s["bundle"]["bundle_id"]=="{target.name}" and s["formal_scope"]=="mysql_only" and s["postgresql_mutation_prohibited"] is True
print("PASS_RETRY20_REAL_RENDERER")
'''.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry20_current_target_seal_renderer_successor_bundle_v1",
                      "SEALED_RETRY20_RENDERER_OFFLINE_NOT_EXECUTED",
                      OLD_RENDERER_ID, OLD_RENDERER_SHA)
    unsigned.update(target_bundle_id=target.name, target_manifest_sha256=target_sha,
                    target_predecessor_bundle_id=OLD_TARGET_ID,
                    target_schema_version=TARGET_SCHEMA,
                    target_collector_sha256=collector_sha,
                    failed_retry19_rerun_authorized=False,
                    resource_gates_relaxed=False)
    return h.publish(RENDERER_OUT, unsigned, content, ".retry20-renderer-")


def build_materializer(target):
    old = h.verify(OLD_MATERIALIZER, OLD_MATERIALIZER_ID, OLD_MATERIALIZER_SHA)
    target_sha = h.sf(target / "bundle_manifest.json")
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (OLD_MATERIALIZER / rel).read_text()
    source = h.once(source,
                    f'TARGET_ID="{OLD_TARGET_ID}";TARGET_SHA="{OLD_TARGET_SHA}"',
                    f'TARGET_ID="{target.name}";TARGET_SHA="{target_sha}"',
                    "Retry20 materializer target")
    content = h.copied(OLD_MATERIALIZER, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry20_current_target_seal_materializer_successor_bundle_v1",
                      "SEALED_RETRY20_MATERIALIZER_OFFLINE_NOT_EXECUTED",
                      OLD_MATERIALIZER_ID, OLD_MATERIALIZER_SHA)
    unsigned.update(target_bundle_id=target.name, target_manifest_sha256=target_sha,
                    expected_sample_count=181, timeout_threshold_relaxed=False)
    return h.publish(MATERIALIZER_OUT, unsigned, content, ".retry20-materializer-")


def build_wrapper(materializer):
    old = h.verify(OLD_WRAPPER, OLD_WRAPPER_ID, OLD_WRAPPER_SHA)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (OLD_WRAPPER / rel).read_text()
    source = h.once(source,
                    f'RETRY_ID="{OLD_MATERIALIZER_ID}";RETRY_SHA="{OLD_MATERIALIZER_SHA}"',
                    f'RETRY_ID="{materializer.name}";RETRY_SHA="{materializer_sha}"',
                    "Retry20 wrapper materializer")
    content = h.copied(OLD_WRAPPER, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry20_current_target_seal_wrapper_successor_bundle_v1",
                      "SEALED_RETRY20_WRAPPER_OFFLINE_NOT_EXECUTED",
                      OLD_WRAPPER_ID, OLD_WRAPPER_SHA)
    unsigned.update(retry5_materializer_bundle_id=materializer.name,
                    retry5_materializer_manifest_sha256=materializer_sha)
    return h.publish(WRAPPER_OUT, unsigned, content, ".retry20-wrapper-")


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
        source = h.once(source, before, after, "Retry20 outer " + label)
    source = h.once(
        source,
        f'FAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT = "tpch-v2-20260804t000500p08-b235retry18rendererfix"',
        f'FAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT = "tpch-v2-20260804t000500p08-b235retry18rendererfix"\nFAILED_RETRY19_TARGET_SEAL_ATTEMPT = "{OLD_ATTEMPT}"',
        "Retry19 failed constant",
    )
    source = h.once(
        source,
        '    if attempt == FAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT:\n'
        '        raise OneClickError("failed Retry18 collector-schema attempt rerun is prohibited")\n'
        '    target = runtime_root / "bundles" / TARGET_ID',
        '    if attempt == FAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT:\n'
        '        raise OneClickError("failed Retry18 collector-schema attempt rerun is prohibited")\n'
        '    if attempt == FAILED_RETRY19_TARGET_SEAL_ATTEMPT:\n'
        '        raise OneClickError("failed Retry19 current-target-seal attempt rerun is prohibited")\n'
        '    target = runtime_root / "bundles" / TARGET_ID',
        "Retry19 rerun prohibition",
    )
    source = h.once(
        source,
        'run_checked([str(runtime_python), "-B", str(paths["support"] / "qualification/mysql_tpch_legacy_blkio_supplement_v1.py"),\n'
        '                     "--bundle", str(target), "--legacy-authority", str(blkio),\n'
        '                     "--container", database, "--output", str(supplement)],',
        'run_checked([str(runtime_python), "-B", str(target / "qualification/mysql_tpch_current_target_blkio_supplement_v2.py"),\n'
        '                     "--bundle", str(target), "--legacy-authority", str(blkio),\n'
        '                     "--container", database, "--expected-target-id", TARGET_ID,\n'
        '                     "--expected-target-manifest-sha256", TARGET_SHA,\n'
        '                     "--output", str(supplement)],',
        "Retry20 current target supplement invocation",
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
        text = content[launch_rel].decode().replace(OLD_ATTEMPT, ATTEMPT).replace(
            "retry19_root_oneclick_", "retry20_root_oneclick_"
        )
        content[launch_rel] = text.encode()
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(
        schema_version="mysql_tpch_retry20_current_target_seal_node04_command_v1",
        attempt_token=ATTEMPT,
        systemd_unit=UNIT,
        target_bundle_id=target.name,
        renderer_bundle_id=renderer.name,
        retry19_rerun_authorized=False,
    )
    content[template_rel] = h.canonical(template)
    failure_rel = "evidence/mysql_tpch_retry19_current_target_seal_failure_v1.json"
    content[failure_rel] = h.canonical({
        "schema_version": "mysql_tpch_retry19_current_target_seal_failure_v1",
        "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token": OLD_ATTEMPT,
        "systemd_unit": OLD_UNIT,
        "failed_at_local": "2026-08-04T15:44:59+08:00",
        "failure_stage": "fresh_blkio_supplement",
        "fresh_error": "fresh blkio supplement failed rc=2 stderr=NOT_READY: exact current-target target seal differs",
        "bootstrap_succeeded": True,
        "database_pid_at_start": 95262,
        "client_pid_at_start": 95578,
        "controller_pid_current": None,
        "workload_pid_current": None,
        "cleanup_confirmation": "PENDING_MAIN_AGENT_REMOTE_READ_ONLY_CLOSURE",
        "rerun_authorized": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "r0_credit": 0,
    })
    test_rel = "tests/test_mysql_tpch_retry20_current_target_seal_control_v1.py"
    content[test_rel] = f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,subprocess,sys,tempfile
B=pathlib.Path(sys.argv[1]).resolve();R=B/"dependencies/bootstrap_renderer/{renderer.name}";T=R/"target/{target.name}";C=B/"{rel}"
subprocess.run([sys.executable,"-B",str(T/"tests/test_mysql_tpch_retry20_target_seal_successor_v1.py"),str(T)],check=True)
subprocess.run([sys.executable,"-B",str(R/"offline_tests/test_mysql_tpch_retry20_real_renderer_v1.py"),str(R)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package()
assert p["renderer"].name=="{renderer.name}" and o.TARGET_ID=="{target.name}" and o.TARGET_SHA=="{tsha}"
assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and o.FAILED_RETRY19_TARGET_SEAL_ATTEMPT=="{OLD_ATTEMPT}"
s=C.read_text();assert 'str(target / "qualification/mysql_tpch_current_target_blkio_supplement_v2.py")' in s
assert '"--expected-target-id", TARGET_ID' in s and '"--expected-target-manifest-sha256", TARGET_SHA' in s
supp=T/"qualification/mysql_tpch_current_target_blkio_supplement_v2.py"
with tempfile.TemporaryDirectory(prefix="retry20-seal-probe-") as raw:
 d=pathlib.Path(raw);authority=d/"blkio_throttle_authority.json";authority.write_text("{{}}\\n");out=d/"blkio_throttle_authority.supplement.json"
 cmd=[sys.executable,"-B",str(supp),"--bundle",str(T),"--legacy-authority",str(authority),"--container","offline","--expected-target-id","{target.name}","--expected-target-manifest-sha256","{tsha}","--output",str(out)]
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);assert p.returncode==2 and "legacy blkio authority sidecar differs" in p.stderr,(p.stdout,p.stderr)
 cmd[cmd.index("{target.name}")]="{OLD_TARGET_ID}";p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);assert p.returncode==2 and "exact current-target target seal differs" in p.stderr,(p.stdout,p.stderr)
m=json.loads((B/"bundle_manifest.json").read_text());assert m["accepted_rounds_credit"]==0 and m["failed_retry19_rerun_authorized"] is False and m["resource_gates_relaxed"] is False
print("PASS_RETRY20_CURRENT_TARGET_SEAL_CONTROL")
'''.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry20_current_target_seal_control_successor_bundle_v1",
                      "SEALED_LOCAL_RETRY20_CURRENT_TARGET_SEAL_NOT_EXECUTED",
                      OLD_OUTER_ID, OLD_OUTER_SHA)
    unsigned.update(
        target_bundle_id=target.name,
        target_manifest_sha256=tsha,
        renderer_bundle_id=renderer.name,
        renderer_manifest_sha256=rsha,
        support_bundle_id=SUPPORT_ID,
        support_manifest_sha256=SUPPORT_SHA,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=msha,
        wrapper_bundle_id=wrapper.name,
        wrapper_manifest_sha256=wsha,
        failed_retry19_attempt=OLD_ATTEMPT,
        failed_retry19_rerun_authorized=False,
        retry19_cleanup_confirmation_required_before_launch=True,
        new_attempt_token=ATTEMPT,
        root_systemd_unit=UNIT,
        current_target_supplement_relpath=SUPPLEMENT_REL,
        current_target_id_sha_passed_by_sealed_outer=True,
        stale_support_target_identity_prohibited=True,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        total_qualification_upper_bound_seconds=600,
        resource_gates_relaxed=False,
        synthetic_or_repeat_samples_prohibited=True,
        failure_propagation_required=True,
    )
    return h.publish(OUTER_OUT, unsigned, content, ".retry20-outer-")


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
