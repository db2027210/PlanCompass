#!/usr/bin/env python3
"""Build an immutable XP successor whose real materials CLI accepts ae795.

Only the local materials producer, its executable regression test, and this
offline builder differ from the predecessor.  Data-plane and Algorithm C
entrypoints remain byte-identical.
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
PARENT_ID = "ae795fa9d1a8a7c4ffa310a75aacd6f0e8a59fa0306a6403d3269aa4478299f2"
PARENT_MANIFEST_SHA256 = "95f26e11d6ff5659c76bab82ee0e95320b0d69bbf86b5c77f214a049c6f8249e"
PARENT = (
    ROOT / "artifacts/mysql_xp_compute00_fourth_invalidation_successor_v11"
    / PARENT_ID
)
OUT = ROOT / "artifacts/mysql_xp_compute00_materials_cli_successor_v12"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
TEST = "tests_compute00/test_mysql_xp_compute00_materials_cli_v12.py"
BUILDER = "builder/build_mysql_xp_compute00_materials_cli_successor_v12.py"
ALLOWED_CHANGED = {MATERIALS, TEST, BUILDER}
INHERITED_TESTS = {
    "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py",
    "tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py",
    "tests_compute00/test_mysql_xp_compute00_sibling_receipt_successor_v8.py",
}
ALLOWED_CHANGED |= INHERITED_TESTS


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


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
        if path.is_file()
        and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def make_writable(root):
    for path in (root, *root.rglob("*")):
        if not path.is_symlink():
            path.chmod(
                stat.S_IMODE(path.stat().st_mode)
                | (0o700 if path.is_dir() else 0o200)
            )


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in successor closure")
        mode = 0o555 if path.is_dir() or (
            path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o111
        ) else 0o444
        path.chmod(mode)


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError("%s replacement count is %d" % (label, text.count(old)))
    return text.replace(old, new, 1)


TEST_SOURCE = r'''import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

BUNDLE = Path(os.environ["XP_BUNDLE_UNDER_TEST"]).resolve()
MATERIALS = BUNDLE / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
PARENT = "ae795fa9d1a8a7c4ffa310a75aacd6f0e8a59fa0306a6403d3269aa4478299f2"


class MaterialsCliV12Tests(unittest.TestCase):
    def test_real_cli_materializes_fresh_zero_credit_attempt(self):
        with tempfile.TemporaryDirectory(prefix="xp-materials-cli-v12-") as tmp:
            output_root = Path(tmp) / "fresh-output-root"
            completed = subprocess.run(
                [sys.executable, "-B", str(MATERIALS), "--bundle", str(BUNDLE),
                 "--output-root", str(output_root), "--valid-minutes", "10",
                 "--root-password-stdin"],
                input="offline-test-password\n", text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED")
            self.assertEqual(result["bundle_id"], BUNDLE.name)
            self.assertFalse(result["formal_r0_authorized"])
            self.assertFalse(result["accepted_round_authorized"])
            attempt = Path(result["materials_directory"])
            self.assertEqual(attempt.parent, output_root.resolve())
            manifest = json.loads((attempt / "materials_manifest.json").read_text())
            self.assertEqual(manifest["accepted_round_authorized"], False)
            self.assertEqual(manifest["invalidated_predecessor_material_credit"], 0)
            spec = json.loads((attempt / "bootstrap_spec.json").read_text())
            self.assertEqual(spec["bundle"]["id"], BUNDLE.name)
            self.assertTrue(spec["cross_engine_comparison_prohibited"])
            for relative in (
                "private_authority/main_agent_token",
                "private_credentials/mysql-root-bootstrap.cnf",
                "private_credentials/mysql-client.cnf",
                "private_credentials/hybench_xp_qualification.properties",
            ):
                self.assertEqual(stat.S_IMODE((attempt / relative).stat().st_mode), 0o600)

    def test_materials_is_bound_to_exact_ae795_predecessor(self):
        text = MATERIALS.read_text(encoding="utf-8")
        self.assertIn('"mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6": PARENT_AE795_BUNDLE_ID', text)
        self.assertIn('PARENT_AE795_BUNDLE_ID = (', text)


if __name__ == "__main__":
    unittest.main()
'''


def patch(stage):
    materials = stage / MATERIALS
    text = materials.read_text(encoding="utf-8")
    anchor = 'LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (\n'
    constant = (
        'PARENT_AE795_BUNDLE_ID = (\n'
        '    "' + PARENT_ID + '"\n'
        ')\n'
    )
    text = replace_once(text, anchor, constant + anchor, "ae795 constant")
    text = replace_once(
        text,
        '"mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6": '
        '"0394a18d03f56fc43b1f4a43d9bc8faf9238af7ac3e1c83f883ee5833a8c3f30",',
        '"mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6": '
        'PARENT_AE795_BUNDLE_ID,',
        "v6 predecessor identity",
    )
    materials.write_text(text, encoding="utf-8")
    for relative in sorted(INHERITED_TESTS):
        inherited = stage / relative
        test_text = inherited.read_text(encoding="utf-8")
        test_text = test_text.replace(
            'PARENT_ID = "c2a15ff45d1368f0496e02ce0b0a4b317e810c1c7306db7f407e1b31eba91dea"',
            'PARENT_ID = "' + PARENT_ID + '"',
        ).replace(
            'PARENT_ID="c2a15ff45d1368f0496e02ce0b0a4b317e810c1c7306db7f407e1b31eba91dea"',
            'PARENT_ID="' + PARENT_ID + '"',
        ).replace(
            'PARENT_MANIFEST_SHA="0f4be8ead8832efc09d1b11395bef594ef9e73a6153b4f8098363f32ea7897e0"',
            'PARENT_MANIFEST_SHA="' + PARENT_MANIFEST_SHA256 + '"',
        )
        inherited.write_text(test_text, encoding="utf-8")
    (stage / TEST).write_text(TEST_SOURCE, encoding="utf-8")
    shutil.copy2(SELF, stage / BUILDER)


def verify(target, bundle_id):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    observed = unsigned.pop("bundle_id", None)
    if (observed != bundle_id
            or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
            or closure(target) != manifest.get("files")):
        raise BuildError("successor canonical closure differs")
    parent_files = json.loads((PARENT / "bundle_manifest.json").read_text())["files"]
    target_files = manifest["files"]
    changed = {
        relative for relative in set(parent_files) | set(target_files)
        if parent_files.get(relative) != target_files.get(relative)
    }
    if changed != ALLOWED_CHANGED:
        raise BuildError("unexpected changed files: %s" % sorted(changed))
    for prefix in ("launch/", "qualification/", "runtime/", "campaign/", "preflight/"):
        for relative, digest in parent_files.items():
            if relative.startswith(prefix) and target_files.get(relative) != digest:
                raise BuildError("data-plane byte changed: " + relative)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target))
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "unittest", "discover", "-s",
         str(target / "tests_compute00"), "-p", "test*.py", "-v"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=300,
    )
    if completed.returncode:
        raise BuildError("full discovery failed\n" + completed.stdout)
    passed = sum(line.rstrip().endswith("... ok")
                 for line in completed.stdout.splitlines())
    if passed < 38 or "OK" not in completed.stdout:
        raise BuildError("full discovery count differs")
    return passed, completed.stdout


def build():
    if sha256(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("ae795 predecessor manifest differs")
    parent_manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".stage-xp-v12-", dir=str(OUT)))
    stage = temporary / "bundle"
    try:
        shutil.copytree(PARENT, stage)
        make_writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch(stage)
        manifest = {key: value for key, value in parent_manifest.items()
                    if key not in ("bundle_id", "files")}
        manifest.update({
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "supersession_reason":
                "materials_cli_exact_ae795_predecessor_binding_and_real_cli_test",
            "materials_cli_successor_version": 12,
            "failed_parent_materials_attempt_credit": 0,
            "failed_parent_materials_reusable": False,
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
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        target = OUT / bundle_id
        if target.exists():
            return target, *verify(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha256(stage / "bundle_manifest.json") + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        freeze(stage)
        stage.chmod(0o755)
        os.rename(stage, target)
        target.chmod(0o555)
        return target, *verify(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if temporary.exists():
            make_writable(temporary)
            shutil.rmtree(temporary)


def main():
    target, passed, output, mode = build()
    print(json.dumps({
        "status": "PASS_IMMUTABLE_V12_MATERIALS_CLI_NOT_LAUNCHED",
        "publication_mode": mode,
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha256(target / "bundle_manifest.json"),
        "tests_compute00_passed": passed,
        "discover_summary": [line for line in output.splitlines()
                             if line.startswith("Ran ") or line == "OK"],
        "data_plane_algorithm_bytes_unchanged": True,
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
