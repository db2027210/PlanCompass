#!/usr/bin/env python3
"""Build minimal d3d successor restoring a sealed root systemd principal."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
D3D_ID = "d3d0bb8048afa3c134458e56da91ae879d5a93fd5fbec1e4d4cd1bd1e13dec22"
D3D_SHA = "371ce278d69f782ef48787b971541fcec64ec8761174685fc38ce293af116939"
ONE_ID = "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3"
ONE_SHA = "88e06baaec02c84e3cebb78cd74bf2e06b03de674118766aac23581d4c7963bd"
BASE = ROOT / "artifacts/mysql_tpch_retry9_frozen_measurement_output_one_click_successor_v1" / D3D_ID
ONE = ROOT / "artifacts/mysql_tpch_retry9_root_principal_failure_one_click_successor_v1" / ONE_ID
OUT = ROOT / "artifacts/mysql_tpch_retry9_root_systemd_principal_successor_v1"
FAILED = "tpch-v2-20260803t110008z-b235retry9d2476ac9135b"
ATTEMPT = "tpch-v2-20260803t110323z-b235retry996180c16f416"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry9_root_oneclick_" + ATTEMPT)
ERROR = "NOT_READY: [Errno 13] Permission denied: /run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock"
C = "controller/mysql_tpch_retry9_one_click_v1.py"
F = "evidence/mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1.json"


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor count differs: {text.count(old)}")
    return text.replace(old, new, 1)


FAILURE = {
    "schema_version": "mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1",
    "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "predecessor_bundle_id": D3D_ID,
    "predecessor_manifest_sha256": D3D_SHA,
    "attempt_token": FAILED,
    "materialization_status": "PASS",
    "launch_argv": ["/bin/sh", "launch_and_wait_retry9.sh"],
    "invoking_principal": {"user": "anonymous", "uid": 1008, "gid": 1008},
    "required_principal": {"systemd_service_user": "root", "uid": 0, "gid": 0},
    "error": ERROR,
    "failure_stage": "oneclick_controller_fourflow_mutex_acquisition_before_bootstrap",
    "mutex": "/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock",
    "pids": {"bootstrap": None, "controller": None, "workload": None},
    "bootstrap_started": False,
    "controller_started": False,
    "workload_started": False,
    "database_or_client_container_started": False,
    "rerun_prohibited": True,
    "formal_launch_authorized": False,
    "accepted_round_authorized": False,
    "accepted_rounds_credit": 0,
}


def controller() -> bytes:
    text = (BASE / C).read_text(encoding="utf-8")
    text = once(text, 'FAILED_MEASUREMENT_ERROR = "measurement output escaped frozen tmpfs"\n',
                'FAILED_MEASUREMENT_ERROR = "measurement output escaped frozen tmpfs"\n'
                f'FAILED_ROOT_LOCK_ATTEMPT = "{FAILED}"\n'
                f'FAILED_ROOT_LOCK_ERROR = "{ERROR}"\n'
                f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"\n'
                f'ROOT_SYSTEMD_UNIT = "{UNIT}"\n'
                f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")\n', "constants")
    text = once(text,
        '        "failure_measurement": root / "evidence/mysql_tpch_retry9_48271876_measurement_tmpfs_escape_failure_exact_v1.json",\n',
        '        "failure_measurement": root / "evidence/mysql_tpch_retry9_48271876_measurement_tmpfs_escape_failure_exact_v1.json",\n'
        f'        "failure_root_lock": root / "{F}",\n', "failure path")
    verify_anchor = '        raise OneClickError("Retry9 measurement tmpfs exact zero-credit evidence differs")\n    return paths\n'
    verify = '''        raise OneClickError("Retry9 measurement tmpfs exact zero-credit evidence differs")
    failure_lock = sealed(paths["failure_root_lock"], "Retry9 d247 root-lock failure")
    if (failure_lock.get("schema_version") != "mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1"
            or failure_lock.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN"
            or failure_lock.get("attempt_token") != FAILED_ROOT_LOCK_ATTEMPT
            or failure_lock.get("error") != FAILED_ROOT_LOCK_ERROR
            or failure_lock.get("pids") != {"bootstrap": None, "controller": None, "workload": None}
            or failure_lock.get("bootstrap_started") is not False
            or failure_lock.get("workload_started") is not False
            or failure_lock.get("rerun_prohibited") is not True
            or failure_lock.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 d247 root-lock exact zero-credit evidence differs")
    return paths
'''
    text = once(text, verify_anchor, verify, "failure verifier")
    text = once(text,
        '    if attempt == FAILED_MEASUREMENT_ATTEMPT:\n        raise OneClickError("failed Retry9 measurement-output attempt rerun is prohibited")\n',
        '    if attempt == FAILED_MEASUREMENT_ATTEMPT:\n        raise OneClickError("failed Retry9 measurement-output attempt rerun is prohibited")\n'
        '    if attempt == FAILED_ROOT_LOCK_ATTEMPT:\n        raise OneClickError("failed Retry9 d247 root-lock attempt rerun is prohibited")\n', "materialize guard")
    text = once(text,
        '        "failed_measurement_evidence": str(paths["failure_measurement"]),\n',
        '        "failed_measurement_evidence": str(paths["failure_measurement"]),\n'
        '        "failed_root_lock_attempt": FAILED_ROOT_LOCK_ATTEMPT,\n'
        '        "failed_root_lock_evidence": str(paths["failure_root_lock"]),\n'
        '        "root_systemd_principal_required": True,\n'
        '        "root_systemd_unit": ROOT_SYSTEMD_UNIT,\n'
        '        "root_systemd_principal_receipt": str(output / "controller/root_systemd_principal.json"),\n', "plan binding")
    text = once(text,
        '            or plan.get("attempt_token") == FAILED_MEASUREMENT_ATTEMPT\n'
        '            or plan.get("accepted_round_authorized") is not False):\n',
        '            or plan.get("attempt_token") == FAILED_MEASUREMENT_ATTEMPT\n'
        '            or plan.get("failed_root_lock_attempt") != FAILED_ROOT_LOCK_ATTEMPT\n'
        '            or plan.get("attempt_token") == FAILED_ROOT_LOCK_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n'
        '            or plan.get("root_systemd_unit") != ROOT_SYSTEMD_UNIT\n'
        '            or plan.get("accepted_round_authorized") is not False):\n', "execute guard")
    text = once(text, '    completion_dir = plan_path.parent.parent / "completion"\n',
        '''    principal = sealed(Path(plan["root_systemd_principal_receipt"]), "root systemd principal")
    if (principal.get("schema_version") != "mysql_tpch_retry9_root_systemd_principal_readback_v1"
            or principal.get("status") != "PASS_EXACT_ROOT_SYSTEMD_PRINCIPAL"
            or principal.get("attempt_token") != plan.get("attempt_token")
            or principal.get("unit") != ROOT_SYSTEMD_UNIT
            or principal.get("effective_uid_gid") != [0, 0]
            or principal.get("main_pid") != os.getpid()
            or principal.get("user") != "root" or principal.get("group") != "root"):
        raise OneClickError("root systemd principal receipt differs")
    completion_dir = plan_path.parent.parent / "completion"
''', "execute principal gate")
    root_code = r'''

def root_systemd_principal_readback(expected_unit: str) -> dict[str, Any]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise OneClickError("one-click root-run requires effective uid/gid 0/0")
    if expected_unit != ROOT_SYSTEMD_UNIT:
        raise OneClickError("root systemd unit identity differs")
    result = subprocess.run(["/usr/bin/systemctl", "show", expected_unit,
        "--property=LoadState", "--property=ActiveState", "--property=SubState",
        "--property=MainPID", "--property=ControlGroup", "--property=User", "--property=Group"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
        env=bytecode_env())
    if result.returncode:
        raise OneClickError("root systemd principal readback failed: " + result.stderr[-800:])
    fields = dict(line.split("=",1) for line in result.stdout.splitlines() if "=" in line)
    cgroup = Path("/proc/self/cgroup").read_text(encoding="ascii")
    if (fields.get("LoadState") != "loaded" or fields.get("ActiveState") not in ("activating","active")
            or fields.get("MainPID") != str(os.getpid()) or fields.get("User") != "root"
            or fields.get("Group") != "root" or expected_unit not in fields.get("ControlGroup", "")
            or expected_unit not in cgroup):
        raise OneClickError("effective root systemd principal identity differs")
    return {"schema_version":"mysql_tpch_retry9_root_systemd_principal_readback_v1",
        "status":"PASS_EXACT_ROOT_SYSTEMD_PRINCIPAL", "attempt_token":ROOT_SYSTEMD_ATTEMPT,
        "unit":expected_unit, "effective_uid_gid":[os.geteuid(),os.getegid()],
        "user":fields["User"], "group":fields["Group"], "main_pid":os.getpid(),
        "control_group":fields["ControlGroup"], "proc_self_cgroup_sha256":hashlib.sha256(cgroup.encode()).hexdigest(),
        "d247_rerun_prohibited":True, "formal_scope":"mysql_only",
        "cross_engine_comparison_prohibited":True, "postgresql_mutation_prohibited":True,
        "accepted_round_authorized":False, "accepted_rounds_credit":0}


def root_run(args: argparse.Namespace) -> int:
    if (args.attempt_token != ROOT_SYSTEMD_ATTEMPT or args.expected_unit != ROOT_SYSTEMD_UNIT
            or args.output_dir != ROOT_SYSTEMD_OUTPUT):
        raise OneClickError("sealed root-run identity differs")
    principal = root_systemd_principal_readback(args.expected_unit)
    materialize(argparse.Namespace(runtime_root=RUNTIME_ROOT, output_dir=args.output_dir,
                                   attempt_token=args.attempt_token, valid_seconds=2400))
    principal_path = args.output_dir / "controller/root_systemd_principal.json"
    seal_new(principal_path, principal, 0o400)
    return execute(argparse.Namespace(
        plan=args.output_dir / "controller/retry9.plan.json",
        authority=args.output_dir / "controller/controller.authority.json",
        token=args.output_dir / "controller/controller.token"))
'''
    text = once(text, '\ndef main() -> int:\n', root_code + '\n\ndef main() -> int:\n', "root-run functions")
    text = once(text,
        '    execution.add_argument("--token", type=Path, required=True)\n'
        '    args = parser.parse_args()\n'
        '    return materialize(args) if args.mode == "materialize" else execute(args)\n',
        '    execution.add_argument("--token", type=Path, required=True)\n'
        '    root_mode = modes.add_parser("root-run")\n'
        '    root_mode.add_argument("--attempt-token", required=True)\n'
        '    root_mode.add_argument("--expected-unit", required=True)\n'
        '    root_mode.add_argument("--output-dir", type=Path, required=True)\n'
        '    args = parser.parse_args()\n'
        '    if args.mode == "materialize": return materialize(args)\n'
        '    if args.mode == "execute": return execute(args)\n'
        '    return root_run(args)\n', "root-run parser")
    return text.encode("utf-8")


INNER = f'''#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
SELF=$(readlink -f -- "$0")
BUNDLE=$(cd -- "$(dirname -- "$SELF")/.." && pwd -P)
PY={RUNTIME}/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
exec "$PY" -B "$BUNDLE/{C}" root-run --attempt-token {ATTEMPT} --expected-unit {UNIT} --output-dir {OUTPUT}
'''.encode()


OUTER = f'''#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
UNIT={UNIT}
SELF=$(readlink -f -- "$0")
BUNDLE=$(cd -- "$(dirname -- "$SELF")/.." && pwd -P)
INNER="$BUNDLE/launch/run_retry9_root_systemd_principal.sh"
STATE=$(/usr/bin/systemctl show "$UNIT" --property=LoadState 2>/dev/null | sed -n 's/^LoadState=//p' || true)
[ "$STATE" = not-found ] || {{ echo "NOT_READY: sealed root one-click unit already exists" >&2; exit 2; }}
/usr/bin/systemd-run --quiet --unit "$UNIT" --service-type=oneshot --remain-after-exit --property=User=root --property=Group=root /bin/sh "$INNER"
i=0
while [ "$i" -lt 900 ]; do
  RAW=$(/usr/bin/systemctl show "$UNIT" --property=LoadState --property=ActiveState --property=SubState --property=ExecMainStatus --property=Result)
  ACTIVE=$(printf '%s\n' "$RAW" | sed -n 's/^ActiveState=//p')
  SUB=$(printf '%s\n' "$RAW" | sed -n 's/^SubState=//p')
  STATUS=$(printf '%s\n' "$RAW" | sed -n 's/^ExecMainStatus=//p')
  RESULT=$(printf '%s\n' "$RAW" | sed -n 's/^Result=//p')
  if [ "$ACTIVE:$SUB:$STATUS:$RESULT" = active:exited:0:success ]; then
    printf 'PASS_ROOT_SYSTEMD_ONECLICK_COMPLETED: attempt=%s unit=%s output=%s\n' '{ATTEMPT}' "$UNIT" '{OUTPUT}'
    exit 0
  fi
  if [ "$ACTIVE" = failed ] || [ "$RESULT" = exit-code ] || [ "$RESULT" = signal ]; then
    echo "NOT_READY: root systemd one-click failed: $RAW" >&2
    exit 2
  fi
  sleep 2
  i=$((i+1))
done
echo 'NOT_READY: root systemd one-click wait timed out' >&2
exit 2
'''.encode()


TEMPLATE = canonical({
    "schema_version": "mysql_tpch_retry9_node04_root_systemd_one_click_command_v1",
    "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "predecessor_d3d_tmpfs_fix_preserved": True,
    "failed_d247_attempt_rerun_prohibited": True,
    "attempt_token": ATTEMPT, "systemd_unit": UNIT,
    "effective_principal": {"user": "root", "group": "root", "uid": 0, "gid": 0},
    "exact_argv": ["/bin/sh", "[THIS_BUNDLE]/launch/launch_and_wait_retry9_root_systemd.sh"],
    "materialize_and_execute_inside_same_root_unit": True,
    "accepted_round_authorized": False, "accepted_rounds_credit": 0,
})


TEST = r'''#!/usr/bin/env python3
import hashlib,importlib.util,json,os,pathlib,stat,subprocess,sys
B=pathlib.Path(sys.argv[1]);C=B/"controller/mysql_tpch_retry9_one_click_v1.py";O=B/"launch/launch_and_wait_retry9_root_systemd.sh";I=B/"launch/run_retry9_root_systemd_principal.sh"
compile(C.read_bytes(),str(C),"exec")
for p in (O,I): subprocess.run(["bash","-n",str(p)],check=True)
s=C.read_text();o=O.read_text();i=I.read_text();f=json.loads((B/"evidence/mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1.json").read_text());m=json.loads((B/"bundle_manifest.json").read_text())
assert f["attempt_token"]=="tpch-v2-20260803t110008z-b235retry9d2476ac9135b" and f["error"]=="NOT_READY: [Errno 13] Permission denied: /run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock" and f["pids"]=={"bootstrap":None,"controller":None,"workload":None} and f["accepted_rounds_credit"]==0
assert 'failed Retry9 d247 root-lock attempt rerun is prohibited' in s and 'root systemd principal receipt differs' in s and 'os.geteuid() != 0 or os.getegid() != 0' in s
assert '--property=User=root --property=Group=root' in o and 'root-run --attempt-token' in i
assert '--value' not in o
assert o.index('/usr/bin/systemd-run') < o.index('while [ "$i" -lt 900 ]')
assert m["predecessor_bundle_id"]=="d3d0bb8048afa3c134458e56da91ae879d5a93fd5fbec1e4d4cd1bd1e13dec22" and m["d3d_tmpfs_fix_preserved"] is True and m["root_systemd_principal_required"] is True
roothelper="dependencies/bootstrap_renderer/a496ad36e202ed7ded0f4910fa16a9690bcc590d7d33de4f574fe7aba63e4226/target/b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47/tpch_root_helper_credential_remediation_v1.json"
assert m["files"][roothelper]=="cb3b2d471b39608a3515b1b01841728329e2e48d6116c0575706f98ada39ab81"
for p in B.rglob("*"):
 assert not p.is_symlink()
 if p.is_file(): assert not stat.S_IMODE(p.stat().st_mode)&0o222
print("PASS_OFFLINE_ROOT_SYSTEMD_SUCCESSOR: d247_sealed d3d_tmpfs_preserved 1ef_root_helper_preserved root_materialize_execute mutex_principal_gate no_pg")
'''.encode()


def main() -> None:
    if sha((BASE / "bundle_manifest.json").read_bytes()) != D3D_SHA:
        raise SystemExit("d3d predecessor manifest differs")
    if sha((ONE / "bundle_manifest.json").read_bytes()) != ONE_SHA:
        raise SystemExit("1ef reference manifest differs")
    content: dict[str, bytes] = {}
    for path in sorted(BASE.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = str(path.relative_to(BASE))
        if rel in ("bundle_manifest.json", "bundle_manifest.json.sha256", C,
                   "templates/node04_one_click_command.json"):
            continue
        content[rel] = path.read_bytes()
    content[C] = controller()
    content["templates/node04_one_click_command.json"] = TEMPLATE
    content["launch/launch_and_wait_retry9_root_systemd.sh"] = OUTER
    content["launch/run_retry9_root_systemd_principal.sh"] = INNER
    failure = canonical(FAILURE)
    content[F] = failure
    content[F + ".sha256"] = (sha(failure) + "  " + Path(F).name + "\n").encode()
    content["authority/invalidated_d247_root_lock_principal_v1.json"] = canonical({
        "schema_version": "mysql_tpch_retry9_d247_root_lock_principal_invalidation_v1",
        "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN",
        "failed_attempt_token": FAILED, "failure_evidence": F,
        "failure_evidence_sha256": sha(failure), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0, "remote_actions": 0,
    })
    content["builder/build_mysql_tpch_retry9_root_systemd_principal_successor_v1.py"] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry9_root_systemd_principal_successor_v1.py"] = TEST
    old = json.loads((BASE / "bundle_manifest.json").read_text())
    files = {name: sha(data) for name, data in sorted(content.items())}
    identity = {k:v for k,v in old.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256")}
    identity.update({
        "schema_version": "mysql_tpch_retry9_root_systemd_principal_successor_bundle_v1",
        "status": "SEALED_D247_FAILURE_ROOT_SYSTEMD_PRINCIPAL_SUCCESSOR_NOT_EXECUTED",
        "predecessor_bundle_id": D3D_ID, "predecessor_manifest_sha256": D3D_SHA,
        "validated_root_principal_reference_bundle_id": ONE_ID,
        "validated_root_principal_reference_manifest_sha256": ONE_SHA,
        "failed_d247_attempt_token": FAILED, "failed_d247_rerun_prohibited": True,
        "invalidated_local_intermediate_bundle_id": "d1bf7076ff310aac47e58b2721fd39b98891c509a16c3bc380b61fd00e2cd117",
        "d3d_tmpfs_fix_preserved": True, "d3d_materializer_bundle_id_preserved": True,
        "d3d_wrapper_bundle_id_preserved": True, "root_systemd_principal_required": True,
        "materialize_and_execute_inside_same_root_unit": True,
        "new_attempt_token": ATTEMPT, "root_systemd_unit": UNIT,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0, "files": files,
    })
    bundle_id = sha(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    raw = canonical(manifest); target = OUT / bundle_id
    OUT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha((target / "bundle_manifest.json").read_bytes()) != sha(raw):
            raise SystemExit("existing successor differs")
        print(json.dumps({"bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":sha(raw)},sort_keys=True)); return
    stage = Path(tempfile.mkdtemp(prefix=".root-systemd-successor-", dir=OUT))
    for rel,data in content.items():
        p=stage/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
        source=BASE/rel
        executable = rel.endswith((".py",".sh")) or (source.exists() and bool(source.stat().st_mode & 0o111))
        p.chmod(0o555 if executable else 0o444)
    (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json").chmod(0o444)
    (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n");(stage/"bundle_manifest.json.sha256").chmod(0o444)
    for base,dirs,_ in os.walk(stage,topdown=False):
        for name in dirs: os.chmod(Path(base)/name,0o555)
    stage.chmod(0o555);os.rename(stage,target)
    print(json.dumps({"bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":sha(raw)},sort_keys=True))


if __name__ == "__main__": main()
