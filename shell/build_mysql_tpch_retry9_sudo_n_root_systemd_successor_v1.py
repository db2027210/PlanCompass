#!/usr/bin/env python3
"""Build the a80 successor with a sealed sudo -n root-systemd launch chain."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
A80_ID = "a80f1917c3c3480a3c02f7125d03d40bb00f60293de6155c08869fd8ee85868c"
A80_SHA = "dba861f88502aeee846499f00e98e9ffebb951fd78c3efcf2743e807d20bc6c6"
D3D_ID = "d3d0bb8048afa3c134458e56da91ae879d5a93fd5fbec1e4d4cd1bd1e13dec22"
D3D_SHA = "371ce278d69f782ef48787b971541fcec64ec8761174685fc38ce293af116939"
BASE = ROOT / "artifacts/mysql_tpch_retry9_root_systemd_principal_successor_v1" / A80_ID
OUT = ROOT / "artifacts/mysql_tpch_retry9_sudo_n_root_systemd_successor_v1"
FAILED = "tpch-v2-20260803t110323z-b235retry996180c16f416"
FAILED_UNIT = "ra-mysql-tpch-oneclick-root-" + FAILED + ".service"
FAILED_ERROR = "Failed to start transient service unit: Interactive authentication required."
ATTEMPT = "tpch-v2-20260803t111835z-b235retry9281acdfda980"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry9_root_oneclick_" + ATTEMPT)
C = "controller/mysql_tpch_retry9_one_click_v1.py"
E = "evidence/mysql_tpch_retry9_a80_systemd_interactive_auth_failure_exact_v1.json"


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
    "schema_version": "mysql_tpch_retry9_a80_systemd_interactive_auth_failure_exact_v1",
    "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "predecessor_bundle_id": A80_ID,
    "predecessor_manifest_sha256": A80_SHA,
    "attempt_token": FAILED,
    "unit": FAILED_UNIT,
    "launch_argv": ["/bin/sh", "[A80_BUNDLE]/launch/launch_and_wait_retry9_root_systemd.sh"],
    "invoking_principal": {"user": "anonymous", "uid": 1008, "gid": 1008},
    "attempted_argv": [
        "/usr/bin/systemd-run", "--quiet", "--unit", FAILED_UNIT,
        "--service-type=oneshot", "--remain-after-exit", "--property=User=root",
        "--property=Group=root", "/bin/sh",
        "[A80_BUNDLE]/launch/run_retry9_root_systemd_principal.sh",
    ],
    "error": FAILED_ERROR,
    "failure_stage": "outer_systemd_run_authorization_before_unit_creation",
    "root_authorization_established": False,
    "sudo_n_used": False,
    "unit_created": False,
    "unit_absent": True,
    "pids": {"bootstrap": None, "controller": None, "workload": None},
    "materialization_started": False,
    "mutex_attempted": False,
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
    old = (
        f'ROOT_SYSTEMD_ATTEMPT = "{FAILED}"\n'
        f'ROOT_SYSTEMD_UNIT = "{FAILED_UNIT}"\n'
        f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry9_root_oneclick_" + FAILED)}")\n'
    )
    new = (
        f'FAILED_SYSTEMD_AUTH_ATTEMPT = "{FAILED}"\n'
        f'FAILED_SYSTEMD_AUTH_ERROR = "{FAILED_ERROR}"\n'
        f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"\n'
        f'ROOT_SYSTEMD_UNIT = "{UNIT}"\n'
        f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")\n'
    )
    text = once(text, old, new, "a80/new identity constants")
    text = once(text,
        '        "failure_root_lock": root / "evidence/mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1.json",\n',
        '        "failure_root_lock": root / "evidence/mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1.json",\n'
        f'        "failure_systemd_auth": root / "{E}",\n', "failure path")
    text = once(text,
        '        raise OneClickError("Retry9 d247 root-lock exact zero-credit evidence differs")\n    return paths\n',
        '''        raise OneClickError("Retry9 d247 root-lock exact zero-credit evidence differs")
    failure_auth = sealed(paths["failure_systemd_auth"], "Retry9 a80 systemd authorization failure")
    if (failure_auth.get("schema_version") != "mysql_tpch_retry9_a80_systemd_interactive_auth_failure_exact_v1"
            or failure_auth.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN"
            or failure_auth.get("attempt_token") != FAILED_SYSTEMD_AUTH_ATTEMPT
            or failure_auth.get("unit") != "ra-mysql-tpch-oneclick-root-" + FAILED_SYSTEMD_AUTH_ATTEMPT + ".service"
            or failure_auth.get("error") != FAILED_SYSTEMD_AUTH_ERROR
            or failure_auth.get("unit_created") is not False
            or failure_auth.get("unit_absent") is not True
            or failure_auth.get("pids") != {"bootstrap": None, "controller": None, "workload": None}
            or failure_auth.get("materialization_started") is not False
            or failure_auth.get("mutex_attempted") is not False
            or failure_auth.get("workload_started") is not False
            or failure_auth.get("rerun_prohibited") is not True
            or failure_auth.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 a80 systemd authorization exact zero-credit evidence differs")
    return paths
''', "failure verifier")
    text = once(text,
        '    if attempt == FAILED_ROOT_LOCK_ATTEMPT:\n        raise OneClickError("failed Retry9 d247 root-lock attempt rerun is prohibited")\n',
        '    if attempt == FAILED_ROOT_LOCK_ATTEMPT:\n        raise OneClickError("failed Retry9 d247 root-lock attempt rerun is prohibited")\n'
        '    if attempt == FAILED_SYSTEMD_AUTH_ATTEMPT:\n        raise OneClickError("failed Retry9 a80 systemd-authorization attempt rerun is prohibited")\n',
        "materialize guard")
    text = once(text,
        '        "failed_root_lock_evidence": str(paths["failure_root_lock"]),\n',
        '        "failed_root_lock_evidence": str(paths["failure_root_lock"]),\n'
        '        "failed_systemd_auth_attempt": FAILED_SYSTEMD_AUTH_ATTEMPT,\n'
        '        "failed_systemd_auth_evidence": str(paths["failure_systemd_auth"]),\n',
        "plan binding")
    text = once(text,
        '            or plan.get("attempt_token") == FAILED_ROOT_LOCK_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n',
        '            or plan.get("attempt_token") == FAILED_ROOT_LOCK_ATTEMPT\n'
        '            or plan.get("failed_systemd_auth_attempt") != FAILED_SYSTEMD_AUTH_ATTEMPT\n'
        '            or plan.get("attempt_token") == FAILED_SYSTEMD_AUTH_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n',
        "execute guard")
    return text.encode("utf-8")


INNER = f'''#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
SELF=$(readlink -f -- "$0")
BUNDLE=$(cd -- "$(dirname -- "$SELF")/.." && pwd -P)
PY={RUNTIME}/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
exec "$PY" -B "$BUNDLE/{C}" root-run --attempt-token {ATTEMPT} --expected-unit {UNIT} --output-dir {OUTPUT}
'''.encode()


OUTER = '''#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
SELF=$(readlink -f -- "$0")
BUNDLE=$(cd -- "$(dirname -- "$SELF")/.." && pwd -P)
ROOT_STARTER="$BUNDLE/launch/root_start_and_wait_retry9_systemd.sh"
exec /usr/bin/sudo -n /bin/sh "$ROOT_STARTER"
'''.encode()


ROOT_STARTER = f'''#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
if [ "$(/usr/bin/id -u)" != 0 ] || [ "$(/usr/bin/id -g)" != 0 ]; then
  echo 'NOT_READY: sealed sudo -n root starter requires effective uid/gid 0/0' >&2
  exit 2
fi
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
    "schema_version": "mysql_tpch_retry9_node04_sudo_n_root_systemd_one_click_command_v1",
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "predecessor_d3d_tmpfs_fix_preserved": True,
    "failed_a80_attempt_rerun_prohibited": True,
    "attempt_token": ATTEMPT,
    "systemd_unit": UNIT,
    "outer_exact_argv": ["/bin/sh", "[THIS_BUNDLE]/launch/launch_and_wait_retry9_root_systemd.sh"],
    "sealed_escalation_argv": ["/usr/bin/sudo", "-n", "/bin/sh", "[THIS_BUNDLE]/launch/root_start_and_wait_retry9_systemd.sh"],
    "sudo_noninteractive": True,
    "tty_prohibited": True,
    "password_prompt_prohibited": True,
    "root_identity_gate_before_systemd_and_materialization": True,
    "materialize_and_execute_inside_same_root_unit": True,
    "accepted_round_authorized": False,
    "accepted_rounds_credit": 0,
})


TEST = r'''#!/usr/bin/env python3
import hashlib,json,pathlib,stat,subprocess,sys
B=pathlib.Path(sys.argv[1]); C=B/"controller/mysql_tpch_retry9_one_click_v1.py"
O=B/"launch/launch_and_wait_retry9_root_systemd.sh"; R=B/"launch/root_start_and_wait_retry9_systemd.sh"; I=B/"launch/run_retry9_root_systemd_principal.sh"
compile(C.read_bytes(),str(C),"exec")
for p in (O,R,I): subprocess.run(["bash","-n",str(p)],check=True)
s=C.read_text(); o=O.read_text(); r=R.read_text(); i=I.read_text(); e=json.loads((B/"evidence/mysql_tpch_retry9_a80_systemd_interactive_auth_failure_exact_v1.json").read_text()); m=json.loads((B/"bundle_manifest.json").read_text())
assert o.count('exec /usr/bin/sudo -n /bin/sh "$ROOT_STARTER"')==1
for bad in ("sudo -S","sudo -A","read -","tty","systemd-run"): assert bad not in o
gate=r.index('$(/usr/bin/id -u)'); assert gate<r.index('/usr/bin/systemctl') and gate<r.index('/usr/bin/systemd-run')
assert '--property=User=root --property=Group=root' in r and '--value' not in r
assert 'root-run --attempt-token tpch-v2-20260803t111835z-b235retry9281acdfda980' in i
assert 'failed Retry9 a80 systemd-authorization attempt rerun is prohibited' in s
assert s.index('os.geteuid() != 0 or os.getegid() != 0') < s.index('materialize(argparse.Namespace')
assert e["error"]=="Failed to start transient service unit: Interactive authentication required." and e["unit_absent"] is True and e["unit_created"] is False
assert e["pids"]=={"bootstrap":None,"controller":None,"workload":None} and e["materialization_started"] is False and e["mutex_attempted"] is False and e["accepted_rounds_credit"]==0
assert m["predecessor_bundle_id"]=="a80f1917c3c3480a3c02f7125d03d40bb00f60293de6155c08869fd8ee85868c" and m["d3d_tmpfs_fix_preserved"] is True
assert m["sudo_path"]=="/usr/bin/sudo" and m["sudo_mode"]=="-n" and m["tty_prohibited"] is True and m["password_prompt_prohibited"] is True
d={k:v for k,v in m["files"].items() if k.startswith("dependencies/")}
assert len(d)==1149 and hashlib.sha256((json.dumps(d,sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest()=="40e589c144eab4d1e089274a87a9e455693baa81e2990bfffb705dec4d3eca45"
for p in B.rglob("*"):
 assert not p.is_symlink()
 if p.is_file(): assert not stat.S_IMODE(p.stat().st_mode)&0o222
print("PASS_OFFLINE_SUDO_N_ROOT_SYSTEMD_SUCCESSOR: a80_sealed sudo_n_exact root_gate_first d3d_chain_preserved no_pg")
'''.encode()


def main() -> None:
    if sha((BASE / "bundle_manifest.json").read_bytes()) != A80_SHA:
        raise SystemExit("a80 predecessor manifest differs")
    content: dict[str, bytes] = {}
    replaced = {
        "bundle_manifest.json", "bundle_manifest.json.sha256", C,
        "templates/node04_one_click_command.json",
        "launch/launch_and_wait_retry9_root_systemd.sh",
        "launch/run_retry9_root_systemd_principal.sh",
    }
    for path in sorted(BASE.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = str(path.relative_to(BASE))
        if rel not in replaced:
            content[rel] = path.read_bytes()
    content[C] = controller()
    content["templates/node04_one_click_command.json"] = TEMPLATE
    content["launch/launch_and_wait_retry9_root_systemd.sh"] = OUTER
    content["launch/root_start_and_wait_retry9_systemd.sh"] = ROOT_STARTER
    content["launch/run_retry9_root_systemd_principal.sh"] = INNER
    failure = canonical(FAILURE)
    content[E] = failure
    content[E + ".sha256"] = (sha(failure) + "  " + Path(E).name + "\n").encode()
    content["authority/invalidated_a80_systemd_interactive_auth_v1.json"] = canonical({
        "schema_version": "mysql_tpch_retry9_a80_systemd_interactive_auth_invalidation_v1",
        "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN",
        "failed_attempt_token": FAILED,
        "failed_unit": FAILED_UNIT,
        "failure_evidence": E,
        "failure_evidence_sha256": sha(failure),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    content["builder/build_mysql_tpch_retry9_sudo_n_root_systemd_successor_v1.py"] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry9_sudo_n_root_systemd_successor_v1.py"] = TEST
    old = json.loads((BASE / "bundle_manifest.json").read_text())
    files = {name: sha(data) for name, data in sorted(content.items())}
    old_deps = {k: v for k, v in old["files"].items() if k.startswith("dependencies/")}
    new_deps = {k: v for k, v in files.items() if k.startswith("dependencies/")}
    if new_deps != old_deps:
        raise SystemExit("a80/d3d dependency chain differs")
    identity = {k: v for k, v in old.items() if k not in (
        "bundle_id", "files", "schema_version", "status", "predecessor_bundle_id",
        "predecessor_manifest_sha256", "new_attempt_token", "root_systemd_unit")}
    identity.update({
        "schema_version": "mysql_tpch_retry9_sudo_n_root_systemd_successor_bundle_v1",
        "status": "SEALED_A80_AUTH_FAILURE_SUDO_N_ROOT_SYSTEMD_SUCCESSOR_NOT_EXECUTED",
        "predecessor_bundle_id": A80_ID,
        "predecessor_manifest_sha256": A80_SHA,
        "d3d_origin_bundle_id": D3D_ID,
        "d3d_origin_manifest_sha256": D3D_SHA,
        "failed_a80_attempt_token": FAILED,
        "failed_a80_unit": FAILED_UNIT,
        "failed_a80_rerun_prohibited": True,
        "failed_a80_unit_absent": True,
        "failed_a80_pids": {"bootstrap": None, "controller": None, "workload": None},
        "invalidated_local_intermediate_bundle_id": "cb6abec739f271bd452ccff330b3e2be00482d9b6abd7544975400cdf068f68b",
        "invalidated_local_intermediate_reason": "offline test implementation defect; never published or executed",
        "d3d_tmpfs_fix_preserved": True,
        "d3d_entire_dependency_chain_preserved": True,
        "sudo_path": "/usr/bin/sudo",
        "sudo_mode": "-n",
        "sudo_noninteractive": True,
        "sudo_stdin_password_prohibited": True,
        "sudo_askpass_prohibited": True,
        "tty_prohibited": True,
        "password_prompt_prohibited": True,
        "outer_escalation_exact_argv": ["/usr/bin/sudo", "-n", "/bin/sh", "[THIS_BUNDLE]/launch/root_start_and_wait_retry9_systemd.sh"],
        "root_identity_gate_before_systemd_run": True,
        "root_identity_gate_before_materialization_and_mutex": True,
        "materialize_and_execute_inside_same_root_unit": True,
        "new_attempt_token": ATTEMPT,
        "root_systemd_unit": UNIT,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    raw = canonical(manifest)
    target = OUT / bundle_id
    OUT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != raw:
            raise SystemExit("existing successor differs")
        print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                          "manifest_sha256": sha(raw)}, sort_keys=True))
        return
    stage = Path(tempfile.mkdtemp(prefix=".sudo-n-root-systemd-successor-", dir=OUT))
    for rel, data in content.items():
        path = stage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        source = BASE / rel
        executable = rel.endswith((".py", ".sh")) or (source.exists() and bool(source.stat().st_mode & 0o111))
        path.chmod(0o555 if executable else 0o444)
    (stage / "bundle_manifest.json").write_bytes(raw)
    (stage / "bundle_manifest.json").chmod(0o444)
    (stage / "bundle_manifest.json.sha256").write_text(sha(raw) + "  bundle_manifest.json\n")
    (stage / "bundle_manifest.json.sha256").chmod(0o444)
    for base, dirs, _ in os.walk(stage, topdown=False):
        for name in dirs:
            os.chmod(Path(base) / name, 0o555)
    stage.chmod(0o555)
    os.rename(stage, target)
    print(json.dumps({"bundle": str(target), "bundle_id": bundle_id,
                      "manifest_sha256": sha(raw)}, sort_keys=True))


if __name__ == "__main__":
    main()
