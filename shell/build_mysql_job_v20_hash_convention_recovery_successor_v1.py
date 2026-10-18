#!/usr/bin/env python3
"""Build JOB v20 after the zero-write v19 hash-convention check failure."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
V19_ID = "111f8bb0d03960bec979e34c21242e51860cba56bc97d04186bb1291baf0b864"
BASE = ROOT / "artifacts/mysql_job_v19_raw_renameat2_recovery_successor_v1" / V19_ID
OLD_TXN = "job-v19-recovery-20260803t102324z-32e5ff1a"
NEW_TXN = "job-v20-recovery-20260803t103444z-22dc974a"
OUT = ROOT / "artifacts/mysql_job_v20_hash_convention_recovery_successor_v1"
NO_NL_KEYS = "e4082666e8b7cda06727ef833ce20b5495c34b5b170e947ec8197accc716f232"
NL_KEYS = "f207ef61500abff778d99148b87e1d110024bb5c8b5f39ff1c8bdb95164c30d1"
NO_NL_CLOSURE = "dfaf1e8656f755f29b3f09bdce58b6141b685689ea4f59e86ca861b44fc0641b"
NL_CLOSURE = "229798d5e19531449852aad3f841abac8988ed1535af4b91c8528be1cb0d32c3"
SCRIPT_NAME = "mysql_job_v20_node04_hash_convention_recovery_20260803t103444z_22dc974a.sh"
MATERIALIZER_NAME = "mysql_job_v20_recovery_input_materializer_v1.py"
PUBLISHER_NAME = "mysql_job_v20_raw_syscall_atomic_tree_publish_v1.py"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor count differs: {text.count(old)}")
    return text.replace(old, new, 1)


def materializer() -> bytes:
    source = BASE / "transaction/mysql_job_v19_recovery_input_materializer_v1.py"
    text = source.read_text(encoding="utf-8")
    text = text.replace(OLD_TXN, NEW_TXN)
    text = text.replace("v19", "v20").replace("V19", "V20")
    text = text.replace(
        'if not V18_PREP.is_dir() or lexists(V18_VISIBLE): raise Error("v18 PREP preservation changed after activation")',
        'if not V18_PREP.is_dir() or lexists(V18_VISIBLE): raise Error("v18 PREP preservation changed after activation")\n'
        ' if next(V19_PARENT.iterdir(),None) is not None or lexists(V19_PREP) or lexists(V19_VISIBLE): raise Error("empty v19 parent preservation changed after activation")',
        1,
    )
    text = text.replace('PARENT=JOB/".relocation_v20"\n',
                        'FAILED_V19_TXN="job-v19-recovery-20260803t102324z-32e5ff1a"\n'
                        'V19_PARENT=JOB/".relocation_v19"\n'
                        'V19_PREP=V19_PARENT/(".prepare-"+FAILED_V19_TXN)\n'
                        'V19_VISIBLE=V19_PARENT/FAILED_V19_TXN\n'
                        'PARENT=JOB/".relocation_v20"\n', 1)
    text = text.replace(f'V18_KEY_LIST_SHA="{NO_NL_KEYS}"\n'
                        f'V18_CLOSURE_SHA="{NO_NL_CLOSURE}"\n',
                        f'V18_KEY_LIST_NO_TRAILING_NEWLINE_SHA="{NO_NL_KEYS}"\n'
                        f'V18_KEY_LIST_TRAILING_NEWLINE_SHA="{NL_KEYS}"\n'
                        f'V18_CLOSURE_NO_TRAILING_NEWLINE_SHA="{NO_NL_CLOSURE}"\n'
                        f'V18_CLOSURE_TRAILING_NEWLINE_SHA="{NL_CLOSURE}"\n', 1)
    anchor = "def v18_authority():\n"
    helper = r'''def receipt_map_hash_conventions(files):
 key_body="\n".join(sorted(files)).encode()
 closure_body="\n".join(k+" "+files[k].get("sha256","") for k in sorted(files)).encode()
 return {"keys_no_trailing_newline":hashlib.sha256(key_body).hexdigest(),
         "keys_trailing_newline":hashlib.sha256(key_body+b"\n").hexdigest(),
         "closure_no_trailing_newline":hashlib.sha256(closure_body).hexdigest(),
         "closure_trailing_newline":hashlib.sha256(closure_body+b"\n").hexdigest()}
'''
    text = replace_once(text, anchor, helper + anchor, "materializer hash helper")
    old_hash = r''' key_payload=("\n".join(sorted(files))+"\n").encode()
 closure_payload=("\n".join(k+" "+files[k].get("sha256","") for k in sorted(files))+"\n").encode()
 if hashlib.sha256(key_payload).hexdigest()!=V18_KEY_LIST_SHA: raise Error("v18 files sorted key-list identity differs")
 if hashlib.sha256(closure_payload).hexdigest()!=V18_CLOSURE_SHA: raise Error("v18 sorted path/hash closure identity differs")
'''
    new_hash = r''' conventions=receipt_map_hash_conventions(files)
 expected_conventions={"keys_no_trailing_newline":V18_KEY_LIST_NO_TRAILING_NEWLINE_SHA,
  "keys_trailing_newline":V18_KEY_LIST_TRAILING_NEWLINE_SHA,
  "closure_no_trailing_newline":V18_CLOSURE_NO_TRAILING_NEWLINE_SHA,
  "closure_trailing_newline":V18_CLOSURE_TRAILING_NEWLINE_SHA}
 if conventions!=expected_conventions: raise Error("v18 receipt map hash conventions differ")
'''
    text = replace_once(text, old_hash, new_hash, "materializer hash authority")
    old_verify = (' exact_dir(V18_PARENT,1008,1008,0o700,"v18 transaction parent");'
                  'exact_dir(V18_PREP,1008,1008,0o700,"preserved failed v18 PREP")\n'
                  ' exact_dir(PARENT,1008,1008,0o700,"v20 transaction parent")\n')
    new_verify = old_verify.split(' exact_dir(PARENT', 1)[0] + (
        ' exact_dir(V19_PARENT,1008,1008,0o700,"preserved empty v19 transaction parent")\n'
        ' if next(V19_PARENT.iterdir(),None) is not None: raise Error("v19 transaction parent is no longer empty")\n'
        ' if lexists(V19_PREP) or lexists(V19_VISIBLE): raise Error("v19 PREP/visible unexpectedly exists")\n'
        ' exact_dir(PARENT,1008,1008,0o700,"v20 transaction parent")\n')
    text = replace_once(text, old_verify, new_verify, "materializer v19 preservation")
    text = text.replace('"v18_files_sorted_key_list_sha256":V18_KEY_LIST_SHA,'
                        '"v18_files_sorted_path_sha256_closure_sha256":V18_CLOSURE_SHA,',
                        '"v18_files_sorted_key_list_sha256":V18_KEY_LIST_TRAILING_NEWLINE_SHA,'
                        '"v18_files_sorted_path_sha256_closure_sha256":V18_CLOSURE_TRAILING_NEWLINE_SHA,'
                        '"v18_files_sorted_key_list_no_trailing_newline_sha256":V18_KEY_LIST_NO_TRAILING_NEWLINE_SHA,'
                        '"v18_files_sorted_path_sha256_closure_no_trailing_newline_sha256":V18_CLOSURE_NO_TRAILING_NEWLINE_SHA,'
                        '"v19_read_only_failure":"NOT_READY: v18 files sorted key-list identity differs",'
                        '"v19_parent_preserved_empty":True,"v19_reuse_prohibited":True,', 1)
    return text.encode("utf-8")


def recovery_script() -> bytes:
    source = BASE / "transaction/mysql_job_v19_node04_raw_renameat2_recovery_20260803t102324z_32e5ff1a.sh"
    text = source.read_text(encoding="utf-8")
    text = text.replace(OLD_TXN, NEW_TXN)
    text = text.replace("v19", "v20").replace("V19", "V20")
    text = text.replace("# Immutable node04 JOB v20 recovery. v17 and v18 are never rerun;",
                        "# Immutable node04 JOB v20 recovery. v17/v18/v19 are never rerun;", 1)
    text = text.replace("mysql_job_v20_raw_renameat2_recovery_successor_plan_v1",
                        "mysql_job_v20_hash_convention_recovery_successor_plan_v1", 1)
    text = text.replace(
        f"readonly TXN='{NEW_TXN}'\n",
        "readonly FAILED_V19_TXN='job-v19-recovery-20260803t102324z-32e5ff1a'\n"
        f"readonly TXN='{NEW_TXN}'\n", 1)
    text = text.replace(
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v20/${TXN}"\n',
        'readonly V19_PARENT="${JOB_ROOT}/.relocation_v19"\n'
        'readonly V19_PREP="${V19_PARENT}/.prepare-${FAILED_V19_TXN}"\n'
        'readonly V19_VISIBLE="${V19_PARENT}/${FAILED_V19_TXN}"\n'
        'readonly DST_TXN="${JOB_ROOT}/.relocation_v20/${TXN}"\n', 1)
    text = text.replace(NO_NL_KEYS, NL_KEYS).replace(NO_NL_CLOSURE, NL_CLOSURE)
    text = text.replace(f'K="{NL_KEYS}"\nC="{NL_CLOSURE}"\n',
                        f'K="{NL_KEYS}"\nC="{NL_CLOSURE}"\n'
                        f'KN="{NO_NL_KEYS}"\nCN="{NO_NL_CLOSURE}"\n', 1)
    text = text.replace(
        ' or d.get("v18_files_sorted_path_sha256_closure_sha256")!=C\n',
        ' or d.get("v18_files_sorted_path_sha256_closure_sha256")!=C\n'
        ' or d.get("v18_files_sorted_key_list_no_trailing_newline_sha256")!=KN\n'
        ' or d.get("v18_files_sorted_path_sha256_closure_no_trailing_newline_sha256")!=CN\n'
        ' or d.get("v19_read_only_failure")!="NOT_READY: v18 files sorted key-list identity differs"\n'
        ' or d.get("v19_parent_preserved_empty") is not True or d.get("v19_reuse_prohibited") is not True\n', 1)
    old_preserve = (
        '  [[ "$TXN" != "$OLD_TXN" && "$TXN" != "$FAILED_V18_TXN" ]] ||\n'
        "    die 'v17/v18 failed predecessor transaction rerun is prohibited'\n")
    new_preserve = (
        '  [[ "$TXN" != "$OLD_TXN" && "$TXN" != "$FAILED_V18_TXN" && "$TXN" != "$FAILED_V19_TXN" ]] ||\n'
        "    die 'v17/v18/v19 failed predecessor transaction rerun is prohibited'\n"
        '  [[ -d "$V19_PARENT" && ! -L "$V19_PARENT" ]] || die \'empty v19 transaction parent was not preserved\'\n'
        '  [[ $(stat -c \'%u:%g:%a\' "$V19_PARENT") == \'1008:1008:700\' ]] || die \'v19 parent owner/mode differs\'\n'
        '  [[ -z $(find "$V19_PARENT" -mindepth 1 -maxdepth 1 -print -quit) ]] || die \'v19 parent is no longer empty\'\n'
        '  require_absent "$V19_PREP"\n'
        '  require_absent "$V19_VISIBLE"\n')
    text = replace_once(text, old_preserve, new_preserve, "recovery v19 preservation")
    return text.encode("utf-8")


def publisher() -> bytes:
    source = BASE / "qualification/mysql_job_v19_raw_syscall_atomic_tree_publish_v1.py"
    return source.read_text(encoding="utf-8").replace("v19", "v20").replace("V19", "V20").encode("utf-8")


def fixture() -> bytes:
    common = ["control.sha256", "segments.list", "segments.sha256",
              "source-pre.json", "source-pre.json.sha256", "source-post.json",
              "source-post.json.sha256", "source-stability.json",
              "source-stability.json.sha256"]
    parts = {"template": [f"archive.tar.gz.part-{i:06d}" for i in range(4)],
             "runtime": ["archive.tar.gz.part-000000"]}
    files = {}
    for index, key in enumerate(sorted(f"{kind}/{name}" for kind in ("template", "runtime")
                                       for name in common + parts[kind])):
        files[key] = {"sha256": hashlib.sha256(("shape:" + str(index)).encode()).hexdigest()}
    return canonical({
        "schema_version": "mysql_job_v18_receipt_files_shape_fixture_v1",
        "note": "The 23 paths and row shape are exact; values are deterministic synthetic hashes.",
        "files": files,
    })


TEST = r'''#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
B=${1:?bundle required}
S="$B/transaction/mysql_job_v20_node04_hash_convention_recovery_20260803t103444z_22dc974a.sh"
M="$B/transaction/mysql_job_v20_recovery_input_materializer_v1.py"
P="$B/qualification/mysql_job_v20_raw_syscall_atomic_tree_publish_v1.py"
F="$B/tests/fixtures/v18_receipt_files_shape_v1.json"
fail(){ printf 'FAIL: %s\n' "$*" >&2; exit 1; }
bash -n "$S"; "$S" --help >/dev/null
python3 -B - "$M" "$P" "$F" <<'PY'
import hashlib,importlib.util,json,pathlib,sys
m,p,f=map(pathlib.Path,sys.argv[1:])
for x in (m,p): compile(x.read_bytes(),str(x),"exec")
spec=importlib.util.spec_from_file_location("v20_materializer",m);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
files=json.loads(f.read_text())["files"]
assert len(files)==23
h=mod.receipt_map_hash_conventions(files)
keys="\n".join(sorted(files)).encode();closure="\n".join(k+" "+files[k]["sha256"] for k in sorted(files)).encode()
assert h=={"keys_no_trailing_newline":hashlib.sha256(keys).hexdigest(),
 "keys_trailing_newline":hashlib.sha256(keys+b"\n").hexdigest(),
 "closure_no_trailing_newline":hashlib.sha256(closure).hexdigest(),
 "closure_trailing_newline":hashlib.sha256(closure+b"\n").hexdigest()}
assert h["keys_no_trailing_newline"]=="e4082666e8b7cda06727ef833ce20b5495c34b5b170e947ec8197accc716f232"
assert h["keys_trailing_newline"]=="f207ef61500abff778d99148b87e1d110024bb5c8b5f39ff1c8bdb95164c30d1"
assert h["keys_no_trailing_newline"]!=h["keys_trailing_newline"] and h["closure_no_trailing_newline"]!=h["closure_trailing_newline"]
PY
rg -F 'V18_CLOSURE_NO_TRAILING_NEWLINE_SHA="dfaf1e8656f755f29b3f09bdce58b6141b685689ea4f59e86ca861b44fc0641b"' "$M" >/dev/null
rg -F 'V18_CLOSURE_TRAILING_NEWLINE_SHA="229798d5e19531449852aad3f841abac8988ed1535af4b91c8528be1cb0d32c3"' "$M" >/dev/null
rg -F 'FAILED_V19_TXN="job-v19-recovery-20260803t102324z-32e5ff1a"' "$M" >/dev/null
rg -F 'next(V19_PARENT.iterdir(),None) is not None' "$M" >/dev/null
rg -F 'v19_reuse_prohibited' "$M" "$S" >/dev/null
rg -F 'PARENT=JOB/".relocation_v20"' "$M" >/dev/null
rg -F 'readonly DST_TXN="${JOB_ROOT}/.relocation_v20/${TXN}"' "$S" >/dev/null
rg -F 'mysql_job_v20_hash_convention_recovery_successor_plan_v1' "$S" >/dev/null
rg -F 'result=fn(316,-100' "$M" >/dev/null
rg -F 'result = syscall(316, -100' "$P" >/dev/null
if rg -n '(^|[;&|[:space:]])(rm|rmdir|unlink|shred|truncate|mv)([[:space:]]|$)' "$S" "$M" "$P"; then fail 'delete/replace command present'; fi
f=$(mktemp -d /tmp/mysql-job-v20-hash-fixture.XXXXXX);trap 'rm -rf -- "$f"' EXIT
python3 -B - "$f" <<'PY'
import ctypes,errno,os,pathlib,sys
r=pathlib.Path(sys.argv[1]);libc=ctypes.CDLL(None,use_errno=True);fn=libc.syscall
fn.argtypes=[ctypes.c_long,ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_long
assert fn(316,-100,os.fsencode(r/"absent-source"),-100,os.fsencode(r/"absent-destination"),1)==-1 and ctypes.get_errno()==errno.ENOENT
PY
printf 'PASS_OFFLINE_V20_FIXTURE: remote_shape_23 both_newline_conventions exact_trailing_authority v19_empty_preserved no_v19_reuse raw_syscall no_delete\n'
'''.encode("utf-8")


def main() -> None:
    material = materializer()
    script = recovery_script()
    pub = publisher()
    shape = fixture()
    authority = {
        "schema_version": "mysql_job_v20_v19_read_only_hash_failure_contract_v1",
        "status": "PASS_EXACT_V19_ZERO_WRITE_FAILURE_SEALED_NOT_EXECUTED",
        "failed_transaction": OLD_TXN,
        "exact_failure": "NOT_READY: v18 files sorted key-list identity differs",
        "failure_phase": "read_only_check_before_PREP_or_write",
        "v19_parent": {"path": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v19",
                         "uid": 1008, "gid": 1008, "mode": "0700", "must_remain_empty": True},
        "v19_prepare_and_visible_must_be_absent": True,
        "v19_rerun_or_reuse_prohibited": True,
        "hash_conventions": {
            "keys_no_trailing_newline_sha256": NO_NL_KEYS,
            "keys_trailing_newline_sha256": NL_KEYS,
            "path_sha256_closure_no_trailing_newline_sha256": NO_NL_CLOSURE,
            "path_sha256_closure_trailing_newline_sha256": NL_CLOSURE,
            "production_authority": "trailing_newline",
            "encoding": "utf-8",
            "ordering": "lexicographic_path",
            "line_format": {"keys": "path", "closure": "path SP sha256"},
        },
        "remote_actions": 0,
        "accepted_rounds_credit": 0,
    }
    plan = {
        "schema_version": "mysql_job_v20_hash_convention_recovery_successor_plan_v1",
        "status": "PASS_IMMUTABLE_RECOVERY_PLAN_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "failed_v19_bundle_id": V19_ID,
        "failed_v19_transaction": OLD_TXN,
        "failed_v19_reuse_prohibited": True,
        "invalidated_local_intermediate_bundle_ids": [
            "4f88595b8bd0f81ea2638102444f86d11c99402189e0bcea8c4214caa5f06fd0",
            "59dcce836157754214db2fd1c88e106155a8d5851710fa0deb13253d530bdbbe",
        ],
        "new_transaction": NEW_TXN,
        "new_transaction_parent": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v20",
        "source": "exact_preserved_v18_PREP_not_v19",
        "minimal_change": "correct_and_seal_both_newline_hash_conventions",
        "input_reuse": "cp_--reflink=always_then_raw_syscall316_RENAME_NOREPLACE",
        "logical_unique_file_bytes": 1077311137,
        "publication": "runtime_then_template_raw_syscall316_RENAME_NOREPLACE",
        "remote_actions": 0,
        "accepted_rounds_credit": 0,
    }
    content = {
        f"transaction/{SCRIPT_NAME}": script,
        f"transaction/{MATERIALIZER_NAME}": material,
        f"qualification/{PUBLISHER_NAME}": pub,
        "authority/v19_read_only_hash_failure_contract.json": canonical(authority),
        "plan/recovery_plan.json": canonical(plan),
        "tests/fixtures/v18_receipt_files_shape_v1.json": shape,
        "tests/test_mysql_job_v20_hash_convention_recovery_successor_v1.sh": TEST,
        "builder/build_mysql_job_v20_hash_convention_recovery_successor_v1.py": Path(__file__).read_bytes(),
    }
    files = {name: sha(data) for name, data in sorted(content.items())}
    identity = dict(plan, files=files, script_sha256=sha(script),
                    materializer_sha256=sha(material), publisher_sha256=sha(pub),
                    authority_sha256=sha(canonical(authority)), fixture_sha256=sha(shape), immutable=True)
    bundle_id = sha(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    manifest_bytes = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists():
        if sha((target / "bundle_manifest.json").read_bytes()) != sha(manifest_bytes):
            raise SystemExit("existing v20 bundle differs")
        print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                          "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script),
                          "materializer_sha256": sha(material), "publisher_sha256": sha(pub)}, sort_keys=True))
        return
    stage = Path(tempfile.mkdtemp(prefix=".mysql_job_v20_recovery.", dir=OUT))
    for relative, data in content.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o555 if relative.endswith((".sh", ".py")) else 0o444)
    (stage / "bundle_manifest.json").write_bytes(manifest_bytes)
    (stage / "bundle_manifest.json").chmod(0o444)
    (stage / "bundle_manifest.json.sha256").write_text(sha(manifest_bytes) + "  bundle_manifest.json\n", encoding="ascii")
    (stage / "bundle_manifest.json.sha256").chmod(0o444)
    for directory, dirs, _ in os.walk(stage, topdown=False):
        for name in dirs: os.chmod(Path(directory) / name, 0o555)
    stage.chmod(0o555)
    os.rename(stage, target)
    print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                      "manifest_sha256": sha(manifest_bytes), "script_sha256": sha(script),
                      "materializer_sha256": sha(material), "publisher_sha256": sha(pub)}, sort_keys=True))


if __name__ == "__main__":
    main()
