#!/usr/bin/env python3
"""Build the minimal XP successor binding the real sibling v12 receipt root."""
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
PARENT_ID = "919bda6248e6b7645b9e27e2883106bde837f8d469445c1a9d9cc84cf1ea066f"
PARENT_MANIFEST_SHA256 = ""
PARENT = ROOT / "artifacts/mysql_xp_compute00_post_adoption_dataplane_v7" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_sibling_receipt_successor_v8"
PREFLIGHT = "preflight/mysql_xp_compute00_disk1_preflight_v2.py"
RUNBOOK = "operator/mysql_xp_compute00_fresh_r0_runbook_v1.md"
TEST = "tests_compute00/test_mysql_xp_compute00_sibling_receipt_successor_v8.py"
SCHEMA = "mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6"
STATUS = "SEALED_COMPUTE00_POST_DAEMON_ADOPTION_DATA_PLANE_NOT_LAUNCHED"
RECEIPT_ROOT = "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files(root):
    return {
        path.relative_to(root).as_posix(): sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def writable(root):
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) |
                       (0o700 if path.is_dir() else 0o200))


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited")
        path.chmod(0o555 if path.is_dir() or
                   (path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o111)
                   else 0o444)


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError("%s patch point count differs: %d" %
                         (label, text.count(old)))
    return text.replace(old, new, 1)


def patch_preflight(stage):
    path = stage / PREFLIGHT
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'RUNTIME=Path("/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803")\n',
        'RUNTIME=Path("/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803")\n'
        'RECEIPT_ROOT=Path("%s")\n' % RECEIPT_ROOT,
        "receipt root constant")
    text = replace_once(
        text,
        ' if {p.name for p in runtime.iterdir()}!={"bundles","authorizations","daemon_adoption_receipts"}:raise PreflightError("runtime root top-level inventory differs")\n bundles=runtime/"bundles";auth=runtime/"authorizations";receipts=runtime/"daemon_adoption_receipts"\n for path in (bundles,auth,receipts):\n',
        ' if {p.name for p in runtime.iterdir()}!={"bundles","authorizations"}:raise PreflightError("runtime root top-level inventory differs")\n bundles=runtime/"bundles";auth=runtime/"authorizations";receipts=RECEIPT_ROOT\n for path in (bundles,auth,receipts):\n',
        "runtime and sibling receipt inventory")
    text = replace_once(
        text,
        'return {"status":"PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY","bundles":identities,"invalidated_authorizations":invalidated,"daemon_adoption_receipt":receipt,"adopted_daemon":daemon,"postgresql_read_only_identity":postgresql,"new_bundle_no_overwrite_required":True,"fresh_attempt_and_authorization_required":True}',
        'return {"status":"PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY","runtime_top_level":["authorizations","bundles"],"daemon_adoption_receipt_root":str(RECEIPT_ROOT),"bundles":identities,"invalidated_authorizations":invalidated,"daemon_adoption_receipt":receipt,"adopted_daemon":daemon,"postgresql_read_only_identity":postgresql,"new_bundle_no_overwrite_required":True,"fresh_attempt_and_authorization_required":True}',
        "inventory evidence")
    path.write_text(text, encoding="utf-8")


def patch_runbook(stage):
    path = stage / RUNBOOK
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "The runtime root must match the sealed post-adoption inventory exactly, including 02b, daemon v7-v12, the v12 root-owned receipt, and the three invalidated zero-credit authorizations.",
        "The runtime root must match the sealed post-adoption inventory exactly, including 02b, daemon v7-v12, and the three invalidated zero-credit authorizations. The v12 root-owned receipt is bound read-only at the real sibling directory `/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts`; it is not copied into the runtime root.",
        "runbook receipt placement")
    text = replace_once(
        text,
        '.runtime_inventory.daemon_adoption_receipt.status == "PASS_EXACT_V12_DAEMON_ADOPTION_RECEIPT"',
        '.runtime_inventory.runtime_top_level == ["authorizations","bundles"] and .runtime_inventory.daemon_adoption_receipt_root == "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts" and .runtime_inventory.daemon_adoption_receipt.status == "PASS_EXACT_V12_DAEMON_ADOPTION_RECEIPT"',
        "runbook jq sibling gate")
    path.write_text(text, encoding="utf-8")


def patch_contracts(stage):
    preflight_sha = sha(stage / PREFLIGHT)
    old_sha = "3a05de2ae3624b2c07b57008947d8e2e80d60642f2d167c3ff4f3ceeccb17839"
    for relative in ("xp_executable_launch_contract_v3.json",
                     "distributed_runtime_contract.json"):
        path = stage / relative
        text = path.read_text(encoding="utf-8")
        if text.count(old_sha) != 1:
            raise BuildError("%s preflight binding count differs" % relative)
        path.write_text(text.replace(old_sha, preflight_sha, 1), encoding="utf-8")


def patch_legacy_executable_test(stage):
    path = stage / "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text,
        'PARENT_ID = "0394a18d03f56fc43b1f4a43d9bc8faf9238af7ac3e1c83f883ee5833a8c3f30"',
        'PARENT_ID = "' + PARENT_ID + '"', "legacy test predecessor")
    text = replace_once(text,
        'self.assertEqual(manifest["status"], "SEALED_COMPUTE00_SYSTEMD219_HYBRID_CGROUP_NOT_LAUNCHED")',
        'self.assertEqual(manifest["status"], "' + STATUS + '")',
        "legacy test status")
    if text.count('PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY') != 2:
        raise BuildError("legacy runtime inventory test count differs")
    text = text.replace('PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY',
                        'PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY')
    path.write_text(text, encoding="utf-8")

    path = stage / "tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text,
        'PARENT_ID="02b460d96dfd69972ca76a2b9620228fc2d239d46453d3c880d8ac001a7f12b4"',
        'PARENT_ID="' + PARENT_ID + '"', "v7 test predecessor")
    text = replace_once(text,
        'PARENT_MANIFEST_SHA="1c5c02d5e6cf1a57e815de50797fda01339827a6e58caef5d0d1d1aea38811be"',
        'PARENT_MANIFEST_SHA="b24d89540eff3e9f6faf81bb4e8079b18bfdc182d7e197bcc813e1945183d32b"',
        "v7 test predecessor manifest")
    text = replace_once(text,
        'if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")',
        'if p.is_file() and p.relative_to(BUNDLE).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")',
        "v7 exact root manifest exclusion")
    path.write_text(text, encoding="utf-8")


TEST_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations
import hashlib,importlib.util,json,os
from pathlib import Path
import unittest

BUNDLE=Path(os.environ.get("XP_BUNDLE_UNDER_TEST",Path(__file__).resolve().parents[1]))
PARENT_ID="919bda6248e6b7645b9e27e2883106bde837f8d469445c1a9d9cc84cf1ea066f"
RECEIPT_ROOT="/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/daemon_adoption_receipts"
def canonical(value):return (json.dumps(value,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
class SiblingReceiptSuccessorTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.manifest=json.loads((BUNDLE/"bundle_manifest.json").read_text())
 def test_content_address_and_closure(self):
  unsigned=dict(self.manifest);observed=unsigned.pop("bundle_id")
  self.assertEqual(observed,BUNDLE.name);self.assertEqual(hashlib.sha256(canonical(unsigned)).hexdigest(),observed)
  actual={p.relative_to(BUNDLE).as_posix():sha(p) for p in sorted(BUNDLE.rglob("*")) if p.is_file() and p.relative_to(BUNDLE).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
  self.assertEqual(actual,self.manifest["files"])
 def test_only_active_runtime_code_change_is_preflight(self):
  changed=self.manifest["active_payload_changes_from_919b"]
  self.assertEqual(changed,["distributed_runtime_contract.json","preflight/mysql_xp_compute00_disk1_preflight_v2.py","xp_executable_launch_contract_v3.json"])
 def test_real_sibling_receipt_binding(self):
  source=(BUNDLE/"preflight/mysql_xp_compute00_disk1_preflight_v2.py").read_text()
  self.assertIn('RECEIPT_ROOT=Path("'+RECEIPT_ROOT+'")',source)
  self.assertIn('!={"bundles","authorizations"}',source)
  self.assertNotIn('runtime/"daemon_adoption_receipts"',source)
  binding=self.manifest["daemon_adoption_receipt_location"]
  self.assertEqual(binding["path"],RECEIPT_ROOT);self.assertEqual(binding["placement"],"sibling_of_runtime_root")
 def test_scope_and_credit(self):
  self.assertEqual(self.manifest["predecessor_bundle_id"],PARENT_ID)
  self.assertEqual(self.manifest["formal_scope"],"mysql_only")
  self.assertTrue(self.manifest["cross_engine_comparison_prohibited"])
  self.assertTrue(self.manifest["postgresql_mutation_prohibited"])
  self.assertFalse(self.manifest["formal_launch_authorized"])
  self.assertFalse(self.manifest["accepted_round_authorized"])
  self.assertEqual(self.manifest["accepted_rounds_credit"],0)
if __name__=="__main__":unittest.main()
'''


def validate_target(target, bundle_id):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if observed != bundle_id or target.name != bundle_id or \
            hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id or \
            files(target) != manifest.get("files"):
        raise BuildError("published closure differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target))
    tests = [
        target / TEST,
        target / "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py",
        target / "tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py",
    ]
    outputs = []
    for test in tests:
        completed = subprocess.run([str(PYTHON), "-B", str(test), "-v"],
                                   cwd=str(ROOT), env=env,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   timeout=180)
        if completed.returncode:
            raise BuildError("sealed test failed:\n" + completed.stdout)
        outputs.append(completed.stdout.strip().splitlines()[-1])
    return outputs


def build():
    if not PARENT.is_dir():
        raise BuildError("919b parent absent")
    parent_sha = sha(PARENT / "bundle_manifest.json")
    parent_manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    if parent_manifest.get("bundle_id") != PARENT_ID:
        raise BuildError("919b parent identity differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-sibling-receipt-v8-",
                                dir=str(OUTPUT)))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage); writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        before = files(stage)
        patch_preflight(stage); patch_runbook(stage); patch_contracts(stage)
        patch_legacy_executable_test(stage)
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_sibling_receipt_successor_v8.py")
        (stage / TEST).write_text(TEST_SOURCE, encoding="utf-8")
        (stage / TEST).chmod(0o755)
        after = files(stage)
        active = [key for key in sorted(set(before) | set(after))
                  if before.get(key) != after.get(key)
                  and (key.startswith("launch/") or key.startswith("qualification/")
                       or key.startswith("runtime/") or key.startswith("preflight/")
                       or key in ("distributed_runtime_contract.json",
                                  "xp_executable_launch_contract_v3.json"))]
        required_active = ["distributed_runtime_contract.json", PREFLIGHT,
                           "xp_executable_launch_contract_v3.json"]
        if active != required_active:
            raise BuildError("active payload change set differs: %r" % active)
        manifest = {key: value for key, value in parent_manifest.items()
                    if key not in ("bundle_id", "files")}
        byte_identical = dict(manifest.get("byte_identical_02b_payload_sha256", {}))
        for relative in ("distributed_runtime_contract.json",
                         "xp_executable_launch_contract_v3.json"):
            byte_identical.pop(relative, None)
        manifest["byte_identical_02b_payload_sha256"] = byte_identical
        manifest.update({
            "schema_version": SCHEMA,
            "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": parent_sha,
            "supersedes_bundle_id": PARENT_ID,
            "supersession_reason": "bind_real_sibling_v12_daemon_adoption_receipt_root",
            "daemon_adoption_receipt_location": {
                "path": RECEIPT_ROOT,
                "placement": "sibling_of_runtime_root",
                "runtime_root_child_prohibited": True,
                "read_only_validation": True,
                "expected_owner": "root:root",
                "expected_directory_mode": "0755",
                "expected_file_mode": "0600",
                "receipt_filename": "feb0662c6d9f8cac7e428ad2d4559f640be3c7c58108d63f84ff5082f3acbf4a.json",
                "receipt_sha256": "6e402264835700d6b8f68f6fa7eab0ceaa30814e006698c6db3494292c4f59b7",
            },
            "controlled_existing_runtime_inventory": dict(
                parent_manifest["controlled_existing_runtime_inventory"],
                runtime_root_exact_top_level=["authorizations", "bundles"],
                daemon_adoption_receipt_root=RECEIPT_ROOT),
            "active_payload_changes_from_919b": active,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": files(stage),
        })
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists():
            return target, validate_target(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(stage / "bundle_manifest.json") + "  bundle_manifest.json\n")
        freeze(stage); stage.chmod(0o755)
        os.rename(stage, target); target.chmod(0o555)
        return target, validate_target(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw); shutil.rmtree(raw)


def main():
    target, outputs, mode = build()
    print(json.dumps({
        "status": "PASS_IMMUTABLE_SIBLING_RECEIPT_SUCCESSOR_NOT_LAUNCHED",
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "publish_mode": mode,
        "tests": outputs,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
