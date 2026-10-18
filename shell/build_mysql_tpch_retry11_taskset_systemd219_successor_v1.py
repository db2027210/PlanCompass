#!/usr/bin/env python3
"""Replace the unsupported systemd-219 CPUAffinity assignment with taskset."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OLD_ID = "527ee0508a19f3392f4db9745c975a3fe1a5de9fa6219095f90dac066767fb5b"
OLD_SHA = "4f368d94685553b4c284f615390abdf051bfe07c35f083d3d9dfa94fccbf0001"
BASE = ROOT / "artifacts/mysql_tpch_a81_phase_prime_successor_v1" / OLD_ID
OUT = ROOT / "artifacts/mysql_tpch_retry11_taskset_systemd219_successor_v1"
FAILED = "tpch-v2-20260803t124500z-b235retry10f6c0a81e4d2b"
ATTEMPT = "tpch-v2-20260803t131500z-b235retry11e07d219a6c4f"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry11_root_oneclick_" + ATTEMPT)
CONTROLLER = "controller/mysql_tpch_retry9_one_click_v1.py"
FAILURE = "evidence/mysql_tpch_retry10_systemd219_cpuaffinity_failure_v1.json"
CPUSET = "16-17,52-53"


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor count differs: {text.count(old)}")
    return text.replace(old, new, 1)


def copy_files(excluded: set[str]) -> dict[str, bytes]:
    result = {}
    for path in sorted(BASE.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(BASE).as_posix()
            if relative not in excluded:
                result[relative] = path.read_bytes()
    return result


def patched_controller() -> bytes:
    text = (BASE / CONTROLLER).read_text(encoding="utf-8")
    old = (f'FAILED_A81_ATTEMPT = "tpch-v2-20260803t111835z-b235retry9281acdfda980"\n'
           f'ROOT_SYSTEMD_ATTEMPT = "{FAILED}"\n'
           f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED}.service"\n'
           f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry10_root_oneclick_" + FAILED)}")')
    new = (f'FAILED_A81_ATTEMPT = "tpch-v2-20260803t111835z-b235retry9281acdfda980"\n'
           f'FAILED_RETRY10_CPUAFFINITY_ATTEMPT = "{FAILED}"\n'
           f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"\n'
           f'ROOT_SYSTEMD_UNIT = "{UNIT}"\n'
           f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")')
    text = once(text, old, new, "retry11 identity")
    text = once(text,
        '        "failure_a81": root / "evidence/mysql_tpch_a81_seq100_failure_zero_credit_v1.json",\n',
        '        "failure_a81": root / "evidence/mysql_tpch_a81_seq100_failure_zero_credit_v1.json",\n'
        f'        "failure_retry10_cpuaffinity": root / "{FAILURE}",\n',
        "retry10 failure path")
    text = once(text,
        '        raise OneClickError("Retry9 a80 systemd authorization exact zero-credit evidence differs")\n    return paths\n',
        '''        raise OneClickError("Retry9 a80 systemd authorization exact zero-credit evidence differs")
    retry10 = sealed(paths["failure_retry10_cpuaffinity"], "Retry10 systemd219 CPUAffinity failure")
    if (retry10.get("schema_version") != "mysql_tpch_retry10_systemd219_cpuaffinity_failure_v1"
            or retry10.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN"
            or retry10.get("attempt_token") != FAILED_RETRY10_CPUAFFINITY_ATTEMPT
            or retry10.get("workload_pid") is not None
            or retry10.get("systemd_error") != "Unknown assignment CPUAffinity=16 17 52 53."
            or retry10.get("accepted_rounds_credit") != 0
            or retry10.get("rerun_prohibited") is not True):
        raise OneClickError("Retry10 CPUAffinity exact zero-credit evidence differs")
    return paths
''', "retry10 failure verifier")
    text = once(text,
        '    if attempt == FAILED_A81_ATTEMPT:\n        raise OneClickError("failed a81 native-metric attempt rerun is prohibited")\n',
        '    if attempt == FAILED_A81_ATTEMPT:\n        raise OneClickError("failed a81 native-metric attempt rerun is prohibited")\n'
        '    if attempt == FAILED_RETRY10_CPUAFFINITY_ATTEMPT:\n        raise OneClickError("failed Retry10 CPUAffinity attempt rerun is prohibited")\n',
        "retry10 materialize guard")
    text = once(text,
        '        "failed_a81_evidence": str(paths["failure_a81"]),\n',
        '        "failed_a81_evidence": str(paths["failure_a81"]),\n'
        '        "failed_retry10_cpuaffinity_attempt": FAILED_RETRY10_CPUAFFINITY_ATTEMPT,\n'
        '        "failed_retry10_cpuaffinity_evidence": str(paths["failure_retry10_cpuaffinity"]),\n',
        "retry10 plan binding")
    text = once(text,
        '            or plan.get("attempt_token") == FAILED_A81_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',
        '            or plan.get("attempt_token") == FAILED_A81_ATTEMPT\n'
        '            or plan.get("failed_retry10_cpuaffinity_attempt") != FAILED_RETRY10_CPUAFFINITY_ATTEMPT\n'
        '            or plan.get("attempt_token") == FAILED_RETRY10_CPUAFFINITY_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n',
        "retry10 execute guard")
    return text.encode("utf-8")


def root_starter() -> bytes:
    text = (BASE / "launch/root_start_and_wait_retry9_systemd.sh").read_text()
    text = text.replace("ra-mysql-tpch-oneclick-root-" + FAILED + ".service", UNIT)
    text = text.replace(FAILED, ATTEMPT)
    text = text.replace("retry10_root_oneclick_" + ATTEMPT,
                        "retry11_root_oneclick_" + ATTEMPT)
    text = once(
        text,
        "--property=User=root --property=Group=root '--property=CPUAffinity=16 17 52 53' /bin/sh \"$INNER\"",
        "--property=User=root --property=Group=root /usr/bin/taskset -c "
        + CPUSET + " /bin/sh \"$INNER\"",
        "systemd219 incompatible CPUAffinity replacement",
    )
    return text.encode("utf-8")


def inner() -> bytes:
    return f'''#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
SELF=$(readlink -f -- "$0")
BUNDLE=$(cd -- "$(dirname -- "$SELF")/.." && pwd -P)
PY={RUNTIME}/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
exec "$PY" -B "$BUNDLE/{CONTROLLER}" root-run --attempt-token {ATTEMPT} --expected-unit {UNIT} --output-dir {OUTPUT}
'''.encode("utf-8")


def compatibility_test(relative: str) -> bytes:
    text = (BASE / relative).read_text(encoding="utf-8")
    text = text.replace("--property=CPUAffinity=16 17 52 53",
                        "/usr/bin/taskset -c 16-17,52-53 /bin/sh")
    text = text.replace(FAILED, ATTEMPT)
    text = text.replace(
        'm["predecessor_bundle_id"]=="042961efb0d52bf29e8410546d4ff3c6122b54b7fcdad11db53b64cfe6b57dbd"',
        f'm["predecessor_bundle_id"]=="{OLD_ID}"',
    )
    return text.encode("utf-8")


FAILURE_VALUE = {
    "schema_version": "mysql_tpch_retry10_systemd219_cpuaffinity_failure_v1",
    "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "predecessor_bundle_id": OLD_ID,
    "predecessor_manifest_sha256": OLD_SHA,
    "attempt_token": FAILED,
    "failed_unit": "ra-mysql-tpch-oneclick-root-" + FAILED + ".service",
    "attempted_systemd_assignment": "CPUAffinity=16 17 52 53",
    "systemd_version": 219,
    "systemd_error": "Unknown assignment CPUAffinity=16 17 52 53.",
    "secondary_error": "Failed to create bus message: No such device or address",
    "workload_pid": None,
    "workload_started": False,
    "resource_gate_started": False,
    "remediation": ["/usr/bin/taskset", "-c", CPUSET, "/bin/sh", "[SEALED_INNER]"],
    "isolation_relaxed": False,
    "rerun_prohibited": True,
    "formal_launch_authorized": False,
    "accepted_round_authorized": False,
    "accepted_rounds_credit": 0,
}


TEST = rf'''#!/usr/bin/env python3
import json,pathlib,stat,subprocess,sys
B=pathlib.Path(sys.argv[1]); R=B/"launch/root_start_and_wait_retry9_systemd.sh"; I=B/"launch/run_retry9_root_systemd_principal.sh"; C=B/"controller/mysql_tpch_retry9_one_click_v1.py"
for p in (R,I): subprocess.run(["bash","-n",str(p)],check=True)
r=R.read_text(); i=I.read_text(); c=C.read_text(); m=json.loads((B/"bundle_manifest.json").read_text())
fixture=json.loads(next(B.glob("dependencies/bootstrap_renderer/*/fixtures/node04_systemd_run_help_fixture_v1.json")).read_text())
assert fixture["systemd_version"]==219 and "--property=NAME=VALUE" in fixture["stdout"] and "COMMAND" in fixture["stdout"]
assert "CPUAffinity=" not in r
assert "/usr/bin/systemd-run --quiet" in r and "--property=User=root --property=Group=root /usr/bin/taskset -c {CPUSET} /bin/sh" in r
probe=subprocess.run(["/usr/bin/taskset","-c","{CPUSET}","/usr/bin/true"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
assert probe.returncode==0,probe.stderr
assert "root-run --attempt-token {ATTEMPT}" in i
assert "failed Retry10 CPUAffinity attempt rerun is prohibited" in c
assert m["controller_affinity_mechanism"]=="systemd219_command_argv_taskset_inheritance" and m["controller_cpuset_cpus"]=="{CPUSET}"
assert m["freshness_threshold_relaxed"] is False and m["timeout_threshold_relaxed"] is False and m["co_resident_conflict_relaxed"] is False
for p in B.rglob("*"):
 assert not p.is_symlink()
 if p.is_file(): assert not stat.S_IMODE(p.stat().st_mode)&0o222
print("PASS_OFFLINE_RETRY11_TASKSET_SYSTEMD219_SUCCESSOR")
'''.encode("utf-8")


def main() -> None:
    if sha((BASE / "bundle_manifest.json").read_bytes()) != OLD_SHA:
        raise SystemExit("Retry10 predecessor manifest differs")
    excluded = {
        "bundle_manifest.json", "bundle_manifest.json.sha256", CONTROLLER,
        "launch/root_start_and_wait_retry9_systemd.sh",
        "launch/run_retry9_root_systemd_principal.sh",
        "templates/node04_one_click_command.json",
        "tests/test_mysql_tpch_retry11_taskset_systemd219_successor_v1.py",
        "tests/test_mysql_tpch_a81_phase_prime_successor_v1.py",
        "tests/test_mysql_tpch_retry9_root_systemd_principal_successor_v1.py",
        "tests/test_mysql_tpch_retry9_sudo_n_root_systemd_successor_v1.py",
    }
    files = copy_files(excluded)
    files[CONTROLLER] = patched_controller()
    files["launch/root_start_and_wait_retry9_systemd.sh"] = root_starter()
    files["launch/run_retry9_root_systemd_principal.sh"] = inner()
    template = json.loads((BASE / "templates/node04_one_click_command.json").read_text())
    template.update({
        "schema_version": "mysql_tpch_retry11_taskset_systemd219_node04_command_v1",
        "attempt_token": ATTEMPT, "systemd_unit": UNIT,
        "failed_retry10_cpuaffinity_attempt_rerun_prohibited": True,
        "controller_affinity_argv": ["/usr/bin/taskset", "-c", CPUSET],
        "systemd_cpuaffinity_assignment_used": False,
    })
    files["templates/node04_one_click_command.json"] = canonical(template)
    failure = canonical(FAILURE_VALUE)
    files[FAILURE] = failure
    files[FAILURE + ".sha256"] = (
        sha(failure) + "  " + Path(FAILURE).name + "\n"
    ).encode("ascii")
    files["tests/test_mysql_tpch_retry11_taskset_systemd219_successor_v1.py"] = TEST
    for relative in (
        "tests/test_mysql_tpch_a81_phase_prime_successor_v1.py",
        "tests/test_mysql_tpch_retry9_root_systemd_principal_successor_v1.py",
        "tests/test_mysql_tpch_retry9_sudo_n_root_systemd_successor_v1.py",
    ):
        files[relative] = compatibility_test(relative)
    files["builder/build_mysql_tpch_retry11_taskset_systemd219_successor_v1.py"] = Path(__file__).read_bytes()
    old = json.loads((BASE / "bundle_manifest.json").read_text())
    identity = {key: value for key, value in old.items()
                if key not in ("bundle_id", "files", "schema_version", "status",
                               "predecessor_bundle_id", "predecessor_manifest_sha256",
                               "new_attempt_token", "root_systemd_unit",
                               "invalidated_local_outer_bundle_id",
                               "invalidated_local_outer_reason")}
    identity.update({
        "schema_version": "mysql_tpch_retry11_taskset_systemd219_successor_bundle_v1",
        "status": "SEALED_LOCAL_RETRY11_TASKSET_SYSTEMD219_NOT_EXECUTED",
        "predecessor_bundle_id": OLD_ID,
        "predecessor_manifest_sha256": OLD_SHA,
        "failed_retry10_attempt_token": FAILED,
        "failed_retry10_rerun_prohibited": True,
        "new_attempt_token": ATTEMPT,
        "root_systemd_unit": UNIT,
        "controller_cpuset_cpus": CPUSET,
        "controller_affinity_mechanism": "systemd219_command_argv_taskset_inheritance",
        "systemd_cpuaffinity_assignment_used": False,
        "systemd_version_bound": 219,
        "taskset_path": "/usr/bin/taskset",
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "co_resident_conflict_relaxed": False,
        "invalidated_local_outer_bundle_id":
            "7139d87fde41285c7eca6d2ae55aaaa1d666a8ca8a3c4255a60ec06ef13c9479",
        "invalidated_local_outer_reason":
            "inherited offline compatibility assertions still required the prohibited CPUAffinity property; never published or executed",
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "files": {name: sha(data) for name, data in sorted(files.items())},
    })
    bundle_id = sha(canonical(identity))
    manifest = canonical(dict(identity, bundle_id=bundle_id))
    target = OUT / bundle_id
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != manifest:
            raise SystemExit("existing Retry11 successor differs")
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".retry11-taskset-", dir=OUT))
        for relative, payload in files.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            original = BASE / relative
            executable = relative.endswith((".py", ".sh")) or (
                original.exists() and bool(original.stat().st_mode & 0o111)
            )
            path.chmod(0o555 if executable else 0o444)
        (stage / "bundle_manifest.json").write_bytes(manifest)
        (stage / "bundle_manifest.json").chmod(0o444)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(manifest) + "  bundle_manifest.json\n", encoding="ascii"
        )
        (stage / "bundle_manifest.json.sha256").chmod(0o444)
        for base, directories, _ in os.walk(stage, topdown=False):
            for directory in directories:
                os.chmod(Path(base) / directory, 0o555)
        stage.chmod(0o555)
        os.rename(stage, target)
    print(json.dumps({
        "bundle": str(target), "bundle_id": bundle_id,
        "manifest_sha256": sha(manifest), "attempt_token": ATTEMPT,
        "unique_launch_argv": ["/bin/sh", str(target / "launch/launch_and_wait_retry9_root_systemd.sh")],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
