#!/usr/bin/env python3
"""Build the immutable XP compute00 JDK-root/long-bootstrap successor.

The failed c4bf attempt is evidence only and is never adopted or reused.  This
offline builder changes bootstrap authority plumbing, not Algorithm C.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "d56108a6150e10cee118ae67f19bbe5c7f6b21a557d96918f4621677d63e5829"
PARENT_MANIFEST_SHA256 = "a2bc6b7a6751faf6ee6c51584f56e389db4dd1a6d8f5b6d6cc40277fe63b07a0"
PARENT = ROOT / "artifacts/mysql_xp_compute00_materials_cli_successor_v12" / PARENT_ID
OUT = ROOT / "artifacts/mysql_xp_compute00_jdk_root_ttl_successor_v13"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
SERVICE = "launch/mysql_xp_compute00_adopted_service_v1.py"
LAUNCH_CONTRACT = "xp_executable_launch_contract_v3.json"
RUNTIME_CONTRACT = "distributed_runtime_contract.json"
TEST = "tests_compute00/test_mysql_xp_compute00_materials_cli_v12.py"
BUILDER = "builder/build_mysql_xp_compute00_jdk_root_ttl_successor_v13.py"
FAILURE = "invalidated_c4bf_jdk_publication_attempt.json"
PARENT_TESTS = {
    "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py",
    "tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py",
    "tests_compute00/test_mysql_xp_compute00_sibling_receipt_successor_v8.py",
}
ALLOWED_CHANGED = {
    MATERIALS, SERVICE, LAUNCH_CONTRACT, RUNTIME_CONTRACT, TEST, BUILDER,
    FAILURE, "tests/test_mysql_xp_authority_ttl_successor_v23.py",
    "tests/test_mysql_xp_ttl_successor_v22.py", *PARENT_TESTS,
}


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def closure(root):
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def writable(root):
    for path in (root, *root.rglob("*")):
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode)
                       | (0o700 if path.is_dir() else 0o200))


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in successor closure")
        executable = path.is_dir() or (
            path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o111
        )
        path.chmod(0o555 if executable else 0o444)


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise BuildError("%s replacement count is %d" % (label, count))
    return text.replace(old, new, 1)


TEST_SOURCE = r'''import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BUNDLE = Path(os.environ["XP_BUNDLE_UNDER_TEST"]).resolve()
MATERIALS = BUNDLE / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
SERVICE = BUNDLE / "launch/mysql_xp_compute00_adopted_service_v1.py"
PARENT = "d56108a6150e10cee118ae67f19bbe5c7f6b21a557d96918f4621677d63e5829"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MaterialsCliV13Tests(unittest.TestCase):
    def test_180_minute_fresh_materials_bind_jdk_to_root_systemd(self):
        with tempfile.TemporaryDirectory(prefix="xp-materials-cli-v13-") as tmp:
            completed = subprocess.run(
                [sys.executable, "-B", str(MATERIALS), "--bundle", str(BUNDLE),
                 "--output-root", tmp, "--valid-minutes", "180",
                 "--root-password-stdin"], input="offline-test-password\n",
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            attempt = Path(result["materials_directory"])
            spec = json.loads((attempt / "bootstrap_spec.json").read_text())
            plan = json.loads((attempt / "command_plan.json").read_text())
            created = datetime.fromisoformat(spec["created_at_utc"])
            expires = datetime.fromisoformat(spec["expires_at_utc"])
            self.assertEqual((expires - created).total_seconds(), 10800)
            self.assertEqual(spec["bundle"]["id"], BUNDLE.name)
            self.assertEqual(plan["effective_uid_required_by_command"]["jdk_publish"], 0)
            self.assertIn("jdk_publish", plan["main_agent_host_root_executor_required_for"])
            self.assertIn("jdk_publish", plan["host_systemd_run_commands_after_separate_authorization"])
            self.assertIn("jdk_publish", plan["controller_poll_after_each_systemd_run"])
            inner = plan["commands_to_run_on_compute00_after_separate_authorization"]["jdk_publish"]
            systemd = plan["host_systemd_run_commands_after_separate_authorization"]["jdk_publish"]
            self.assertEqual(systemd[-len(inner):], inner)
            self.assertIn("jdk", spec["attempt"]["executor_units"])
            self.assertFalse(result["formal_r0_authorized"])
            self.assertFalse(result["accepted_round_authorized"])

    def test_service_accepts_180_minutes_but_rejects_over_240(self):
        service = load(SERVICE, "xp_v13_service")
        now = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
        created = now - timedelta(seconds=1)
        exact = created + timedelta(minutes=180)
        self.assertEqual(
            service.verify_short_lived_authority(
                created.isoformat(), exact.isoformat(), now=now
            )["expires_at_utc"], exact.isoformat()
        )
        with self.assertRaisesRegex(service.ServiceError, "exceeds 240 minutes"):
            service.verify_short_lived_authority(
                created.isoformat(),
                (created + timedelta(minutes=240, seconds=1)).isoformat(),
                now=now,
            )

    def test_failed_c4bf_is_evidence_only(self):
        failure = json.loads((BUNDLE / "invalidated_c4bf_jdk_publication_attempt.json").read_text())
        self.assertEqual(failure["status"], "INVALID_ZERO_CREDIT_DO_NOT_REUSE")
        self.assertFalse(failure["physical_copy_inheritable_by_successor_attempt"])
        self.assertEqual(failure["accepted_rounds_credit"], 0)
        self.assertEqual(json.loads((BUNDLE / "bundle_manifest.json").read_text())["predecessor_bundle_id"], PARENT)


if __name__ == "__main__":
    unittest.main()
'''


def patch(stage):
    materials = stage / MATERIALS
    text = materials.read_text()
    text = replace_once(
        text,
        'PARENT_AE795_BUNDLE_ID = (\n    "ae795fa9d1a8a7c4ffa310a75aacd6f0e8a59fa0306a6403d3269aa4478299f2"\n)\n',
        'PARENT_AE795_BUNDLE_ID = (\n    "ae795fa9d1a8a7c4ffa310a75aacd6f0e8a59fa0306a6403d3269aa4478299f2"\n)\nPARENT_D561_BUNDLE_ID = (\n    "' + PARENT_ID + '"\n)\n',
        "d561 constant",
    )
    text = replace_once(
        text,
        '"mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6": PARENT_AE795_BUNDLE_ID,',
        '"mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6": PARENT_D561_BUNDLE_ID,',
        "supported successor identity",
    )
    text = replace_once(text, "not 10 <= valid_minutes <= 60",
                        "not 180 <= valid_minutes <= 240", "materials TTL bound")
    text = replace_once(text, "valid minutes must be within 10..60",
                        "valid minutes must be within 180..240", "materials TTL error")
    text = replace_once(
        text,
        'for action in ("copy", "plan", "bootstrap", "adopt")',
        'for action in ("copy", "jdk", "plan", "bootstrap", "adopt")',
        "JDK executor unit",
    )
    text = replace_once(
        text,
        r'ra-mysql-xp-v12-(copy|plan|bootstrap|adopt)-[0-9a-f]{16}\.service',
        r'ra-mysql-xp-v12-(copy|jdk|plan|bootstrap|adopt)-[0-9a-f]{16}\.service',
        "JDK transient unit validator",
    )
    anchor = '    physical_copy = with_unit(\n'
    jdk = (
        '    jdk_publish = prefix + [\n'
        '        remote_bundle + "/runtime/mysql_xp_compute00_jdk_publish_v1.py",\n'
        '        "--bundle", remote_bundle,\n'
        '        "--output", spec["paths"]["jdk_publication"],\n'
        '    ]\n'
    )
    text = replace_once(text, anchor, jdk + anchor, "JDK inner argv")
    text = replace_once(
        text, '"physical_copy": 0,\n            "bootstrap": 0,',
        '"physical_copy": 0,\n            "jdk_publish": 0,\n            "bootstrap": 0,',
        "JDK effective UID",
    )
    text = replace_once(
        text, '"physical_copy", "bootstrap",\n        ],',
        '"physical_copy", "jdk_publish", "bootstrap",\n        ],',
        "JDK root executor list",
    )
    old_jdk = '''            "jdk_publish": prefix + [
                remote_bundle + "/runtime/mysql_xp_compute00_jdk_publish_v1.py",
                "--bundle", remote_bundle,
                "--output", spec["paths"]["jdk_publication"],
            ],'''
    text = replace_once(text, old_jdk, '            "jdk_publish": jdk_publish,',
                        "shared JDK argv")
    text = replace_once(
        text, '"physical_copy": systemd_run("copy", physical_copy),',
        '"physical_copy": systemd_run("copy", physical_copy),\n'
        '            "jdk_publish": systemd_run("jdk", jdk_publish),',
        "JDK systemd run",
    )
    text = replace_once(
        text, '"physical_copy": controller_poll("copy", physical_copy),',
        '"physical_copy": controller_poll("copy", physical_copy),\n'
        '            "jdk_publish": controller_poll("jdk", jdk_publish),',
        "JDK controller poll",
    )
    text = replace_once(
        text,
        '"nonroot_direct_execution_of_physical_copy_or_bootstrap_prohibited":\n            True,',
        '"nonroot_direct_execution_of_physical_copy_jdk_or_bootstrap_prohibited":\n            True,',
        "nonroot JDK prohibition",
    )
    materials.write_text(text)

    service = stage / SERVICE
    text = service.read_text()
    text = replace_once(text, "duration > 3600", "duration > 14400",
                        "service TTL max")
    text = replace_once(text, "validity exceeds 60 minutes",
                        "validity exceeds 240 minutes", "service TTL error")
    service.write_text(text)
    new_service_sha = sha256(service)

    for relative in (LAUNCH_CONTRACT, RUNTIME_CONTRACT):
        path = stage / relative
        value = path.read_text()
        if value.count("90810747aedf5eb8fa3c4bca9f4653a9870c73f20892aeb118099d4f845dd002") < 1:
            raise BuildError("old service SHA absent from " + relative)
        path.write_text(value.replace(
            "90810747aedf5eb8fa3c4bca9f4653a9870c73f20892aeb118099d4f845dd002",
            new_service_sha,
        ))

    for relative in PARENT_TESTS:
        path = stage / relative
        value = path.read_text().replace(
            "ae795fa9d1a8a7c4ffa310a75aacd6f0e8a59fa0306a6403d3269aa4478299f2",
            PARENT_ID,
        ).replace(
            "95f26e11d6ff5659c76bab82ee0e95320b0d69bbf86b5c77f214a049c6f8249e",
            PARENT_MANIFEST_SHA256,
        )
        if relative.endswith("test_mysql_xp_compute00_post_adoption_dataplane_v7.py"):
            value = replace_once(
                value,
                'for relative,digest in preserved.items():self.assertEqual(sha(BUNDLE/relative),digest,relative)',
                'for relative,digest in preserved.items():\n'
                '   if relative != "launch/mysql_xp_compute00_adopted_service_v1.py": self.assertEqual(sha(BUNDLE/relative),digest,relative)\n'
                '  self.assertEqual(sha(BUNDLE/"launch/mysql_xp_compute00_adopted_service_v1.py"),self.manifest["service_sha256"])',
                "service authorization plumbing exception",
            )
        path.write_text(value)

    ttl = stage / "tests/test_mysql_xp_ttl_successor_v22.py"
    value = ttl.read_text().replace("exact_60_minute", "exact_180_minute")
    value = value.replace("tmp_path, 60,", "tmp_path, 180,")
    value = value.replace("== 3600", "== 10800")
    value = value.replace("(9, 61)", "(179, 241)")
    value = value.replace("10..60", "180..240")
    ttl.write_text(value)

    auth_test = stage / "tests/test_mysql_xp_authority_ttl_successor_v23.py"
    value = auth_test.read_text().replace("3600", "14400").replace(
        "3601", "14401").replace("60 minutes", "240 minutes")
    auth_test.write_text(value)

    (stage / TEST).write_text(TEST_SOURCE)
    (stage / FAILURE).write_bytes(canonical({
        "schema_version": "mysql_xp_compute00_failed_attempt_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT_DO_NOT_REUSE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation": False,
        "attempt_id": "xp-formal-qual-20260803T121312Z-c4bf5db31b097a5fbfbc6b6e",
        "predecessor_bundle_id": PARENT_ID,
        "failure_stage": "jdk_publish_after_successful_physical_copy",
        "failure": "PermissionError creating attempt-root jdk_publication temporary file from nonroot executor",
        "physical_copy_unit": "ra-mysql-xp-v12-copy-c744221fc4e5c588.service",
        "physical_copy_unit_terminal_state": {
            "ActiveState": "active", "SubState": "exited", "MainPID": 0,
            "ExecMainStatus": 0, "Result": "success",
        },
        "physical_copy_authority_sha256": "56dc9ae05c3a3520f793a5cee840aea1ab4a786744c89b92376c0a861d574e96",
        "physical_copy_authority_status": "PASS_BYTE_COPY_FIEMAP_AND_MYSQL_UID_NORMALIZATION",
        "physical_copy_authority_attempt_bound": True,
        "physical_copy_authority_target_datadir_bound": True,
        "physical_copy_inheritable_by_successor_attempt": False,
        "attempt_reuse_prohibited": True,
        "authorization_reuse_prohibited": True,
        "accepted_rounds_credit": 0,
    }))
    shutil.copy2(SELF, stage / BUILDER)
    return new_service_sha


def verify(target, bundle_id):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    observed = unsigned.pop("bundle_id", None)
    if observed != bundle_id or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id:
        raise BuildError("canonical bundle ID differs")
    if closure(target) != manifest.get("files"):
        raise BuildError("bundle closure differs")
    parent_files = json.loads((PARENT / "bundle_manifest.json").read_text())["files"]
    changed = {name for name in set(parent_files) | set(manifest["files"])
               if parent_files.get(name) != manifest["files"].get(name)}
    if changed != ALLOWED_CHANGED:
        raise BuildError("unexpected changed files: %s" % sorted(changed))
    for prefix in ("campaign/", "qualification/", "runtime/", "profile/"):
        for name, digest in parent_files.items():
            if name.startswith(prefix) and manifest["files"].get(name) != digest:
                raise BuildError("Algorithm C/data-plane file changed: " + name)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target))
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "unittest", "discover", "-s",
         str(target / "tests_compute00"), "-p", "test*.py", "-v"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=300,
    )
    if completed.returncode:
        raise BuildError("compute00 test discovery failed\n" + completed.stdout)
    passed = sum(line.rstrip().endswith("... ok")
                 for line in completed.stdout.splitlines())
    if passed < 39 or "OK" not in completed.stdout:
        raise BuildError("compute00 test count differs\n" + completed.stdout)
    return passed, completed.stdout


def build():
    if sha256(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("d561 predecessor manifest differs")
    parent_manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".stage-xp-v13-", dir=str(OUT)))
    stage = temporary / "bundle"
    try:
        shutil.copytree(PARENT, stage)
        writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        service_sha = patch(stage)
        manifest = {key: value for key, value in parent_manifest.items()
                    if key not in ("bundle_id", "files")}
        manifest.update({
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "supersession_reason": "jdk_publication_root_systemd_and_180m_fresh_bootstrap_authority",
            "materials_cli_successor_version": 13,
            "service_sha256": service_sha,
            "sealed_service_authority_valid_seconds_max": 14400,
            "materials_authority_valid_minutes_min": 180,
            "materials_authority_valid_minutes_max": 240,
            "failed_c4bf_attempt_credit": 0,
            "failed_c4bf_attempt_reusable": False,
            "failed_c4bf_physical_copy_inheritable": False,
            "jdk_publish_effective_uid": 0,
            "jdk_publish_pid1_transient_systemd_required": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": closure(stage),
        })
        payload = dict(manifest.get("byte_identical_02b_payload_sha256") or {})
        if "launch/mysql_xp_compute00_adopted_service_v1.py" in payload:
            payload["launch/mysql_xp_compute00_adopted_service_v1.py"] = service_sha
        manifest["byte_identical_02b_payload_sha256"] = payload
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        target = OUT / bundle_id
        if target.exists():
            return target, *verify(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha256(stage / "bundle_manifest.json") + "  bundle_manifest.json\n")
        freeze(stage)
        stage.chmod(0o755)
        os.rename(stage, target)
        target.chmod(0o555)
        return target, *verify(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if temporary.exists():
            writable(temporary)
            shutil.rmtree(temporary)


def main():
    target, passed, output, mode = build()
    print(json.dumps({
        "status": "PASS_IMMUTABLE_V13_JDK_ROOT_TTL_NOT_LAUNCHED",
        "publication_mode": mode,
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha256(target / "bundle_manifest.json"),
        "tests_compute00_passed": passed,
        "discover_summary": [line for line in output.splitlines()
                             if line.startswith("Ran ") or line == "OK"],
        "new_materials_valid_minutes": 180,
        "fresh_physical_copy_required": True,
        "formal_scope": "mysql_only",
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
