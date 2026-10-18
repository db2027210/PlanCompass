#!/usr/bin/env python3
"""Build an immutable a81 successor with one-sample native phase priming.

The a81 collector emitted sequence N only 250 ms before the collector's exact
sequence-N read.  The native SQL producer has its own 1 Hz phase, so that lead
does not guarantee ordering.  This successor consumes no stale repeat and
changes neither the frozen freshness gate nor the 2.0 s producer timeout.  It
waits for native marker 1 under the existing timeout before starting the
collector epoch, leaving sequences 0 and 1 sealed before the first two reads.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
A81_ID = "042961efb0d52bf29e8410546d4ff3c6122b54b7fcdad11db53b64cfe6b57dbd"
A81_SHA = "24041d1fafd0858939dde8222d2f03625a45556681d7aa4989f34c570cb4e5d3"
OLD_SUPPORT_ID = "947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
OLD_SUPPORT_SHA = "1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7"
BASE = ROOT / "artifacts/mysql_tpch_retry9_sudo_n_root_systemd_successor_v1" / A81_ID
OLD_SUPPORT = BASE / "dependencies/support" / OLD_SUPPORT_ID
OUT = ROOT / "artifacts/mysql_tpch_a81_phase_prime_successor_v1"
FAILED_ATTEMPT = "tpch-v2-20260803t111835z-b235retry9281acdfda980"
ATTEMPT = "tpch-v2-20260803t124500z-b235retry10f6c0a81e4d2b"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry10_root_oneclick_" + ATTEMPT)
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
CONTROLLER = "controller/mysql_tpch_retry9_one_click_v1.py"
FAILURE = "evidence/mysql_tpch_a81_seq100_failure_zero_credit_v1.json"


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label} anchor count differs: {count}")
    return text.replace(old, new, 1)


def manifest_bytes(identity: dict, files: dict[str, bytes]) -> tuple[str, bytes]:
    unsigned = dict(identity, files={name: sha(data) for name, data in sorted(files.items())})
    bundle_id = sha(canonical(unsigned))
    return bundle_id, canonical(dict(unsigned, bundle_id=bundle_id))


def materialize(out: Path, bundle_id: str, manifest: bytes,
                files: dict[str, bytes], source: Path) -> Path:
    target = out / bundle_id
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != manifest:
            raise RuntimeError("existing immutable successor differs")
        return target
    out.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".phase-prime-", dir=out))
    for relative, payload in files.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        original = source / relative
        executable = relative.endswith((".py", ".sh")) or (
            original.exists() and bool(original.stat().st_mode & 0o111)
        )
        path.chmod(0o555 if executable else 0o444)
    (stage / "bundle_manifest.json").write_bytes(manifest)
    (stage / "bundle_manifest.json").chmod(0o444)
    sidecar = sha(manifest) + "  bundle_manifest.json\n"
    (stage / "bundle_manifest.json.sha256").write_text(sidecar, encoding="ascii")
    (stage / "bundle_manifest.json.sha256").chmod(0o444)
    for base, directories, _ in os.walk(stage, topdown=False):
        for directory in directories:
            os.chmod(Path(base) / directory, 0o555)
    stage.chmod(0o555)
    os.rename(stage, target)
    return target


def copied_files(source: Path, excluded: set[str]) -> dict[str, bytes]:
    result = {}
    for path in sorted(source.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(source).as_posix()
            if relative not in excluded:
                result[relative] = path.read_bytes()
    return result


def patched_core() -> bytes:
    text = (OLD_SUPPORT / CORE).read_text(encoding="utf-8")
    helper = '''\n\ndef _prime_native_metric_one_sample_ahead(\n    native_adapter_kind, stdout_path, metric_path, attempt_id,\n    previous_native_epoch_ms,\n):\n    """Seal seq1 before the collector epoch under the frozen strict timeout."""\n    return metric_adapter.emit_fresh_sample(\n        native_adapter_kind,\n        stdout_path,\n        metric_path,\n        attempt_id,\n        1,\n        previous_native_epoch_ms,\n        timeout_seconds=2.0,\n    )\n'''
    text = once(
        text,
        "\ndef run_qualification(\n",
        helper + "\n\ndef run_qualification(\n",
        "phase-prime helper insertion",
    )
    old = '''            # Bind both emission and collection to one monotonic epoch.\n            # Sequence zero was emitted synchronously above.  The remaining\n            # exact suffix is emitted at 1 Hz with a 250 ms producer lead; this\n            # prevents a fast native query stream from exhausting seq180 before\n            # the final collector tick without permitting stale repeats.\n            started = time.monotonic()\n            def produce_native_metrics():\n                sequence = 1\n'''
    new = '''            # Prime exactly one strict native marker ahead before binding the\n            # collector epoch.  a81's 250 ms scheduled lead could coincide with\n            # the independent native 1 Hz phase and race the exact consumer.\n            # This wait retains the frozen 2.0 s timeout and freshness limit;\n            # a genuinely missing/stale marker still fails closed.\n            try:\n                native_previous_epoch_ms = (\n                    _prime_native_metric_one_sample_ahead(\n                        native_adapter_kind, stdout_path, metric_path,\n                        spec["qualification_attempt_id"],\n                        native_previous_epoch_ms,\n                    )\n                )\n            except metric_adapter.WorkloadMetricAdapterError as exc:\n                raise QualificationError(\n                    "native metric phase prime failed: %s" % exc\n                )\n            started = time.monotonic()\n            def produce_native_metrics():\n                sequence = 2\n'''
    text = once(text, old, new, "a81 producer/consumer phase race")
    return text.encode("utf-8")


def patched_entry() -> bytes:
    text = (OLD_SUPPORT / ENTRY).read_text(encoding="utf-8")
    helper = r'''

def _classify_verified_local_controller_ancestors(result):
    """Reclassify only the collector's sealed Retry5 controller ancestry.

    systemd reparents the Retry5 controller to PID 1.  The core sees that
    exact controller as co-resident even though it launched this collector.
    Bind it through the sealed Retry5 plan and current attempt; do not match
    names, CPU overlap, or arbitrary foreign processes.
    """
    rows = result.get("processes") or []
    by_pid = {int(row.get("pid") or 0): row for row in rows}
    own_pid = os.getpid()
    own_row = by_pid.get(own_pid)
    if own_row is None:
        raise core.QualificationError("own collector process row is absent")
    own_argv = _proc_argv_and_hash(own_pid, own_row.get("cmdline_sha256"))
    command = _sealed_collector_command(own_argv)
    attempt = command.get("qualification_attempt_id")
    cursor = int(own_row.get("ppid") or 0)
    seen = set()
    matched = []
    while cursor > 1 and cursor not in seen:
        seen.add(cursor)
        row = by_pid.get(cursor)
        if row is None:
            raise core.QualificationError(
                "collector controller ancestor row is absent"
            )
        argv = _proc_argv_and_hash(cursor, row.get("cmdline_sha256"))
        if (len(argv) >= 3
                and Path(argv[1]).name
                == "mysql_tpch_retry5_systemd219_completion_v1.py"
                and "execute" in argv):
            plan_path = Path(_exact_option(argv, "--plan")).resolve(strict=True)
            if not core.valid_sidecar(plan_path):
                raise core.QualificationError(
                    "Retry5 controller plan seal differs"
                )
            plan = core.load_object(plan_path, "Retry5 controller plan")
            launch = plan.get("launch_argv") or []
            if (plan.get("schema_version")
                    != "mysql_tpch_retry5_systemd219_plan_v1"
                    or plan.get("qualification_attempt_id") != attempt
                    or Path(str(plan.get("collector_bundle") or "")).resolve()
                    != HERE.parent.resolve()
                    or str(Path(__file__).resolve()) not in launch
                    or plan.get("accepted_round_authorized") is not False):
                raise core.QualificationError(
                    "Retry5 controller lineage authority differs"
                )
            matched.append(cursor)
        if (row.get("container_identity") is not None
                or int(row.get("declared_device_writable_fds") or 0) != 0):
            raise core.QualificationError(
                "collector controller ancestor isolation differs"
            )
        row["role"] = "qualification_control_plane"
        row["controller_lineage_authority"] = (
            "sealed_retry5_plan_plus_proc_parent_chain"
        )
        cursor = int(row.get("ppid") or 0)
    if len(matched) != 1:
        raise core.QualificationError(
            "Retry5 controller ancestor cardinality differs"
        )
    result["verified_local_controller_ancestor_pids"] = matched
    result["local_controller_lineage_exact_authority_verified"] = True
    return result
'''
    text = once(text, "\ndef _numeric_proc_pid_set():\n",
                helper + "\n\ndef _numeric_proc_pid_set():\n",
                "controller ancestor classifier insertion")
    old = '''        result = _mark_pid_set_consistency(result, before, after)\n        result = _classify_two_verified_daemons(\n            result, args[4], args[5], docker_containers\n        )\n'''
    new = '''        result = _mark_pid_set_consistency(result, before, after)\n        result = _classify_verified_local_controller_ancestors(result)\n        result = _classify_two_verified_daemons(\n            result, args[4], args[5], docker_containers\n        )\n'''
    text = once(text, old, new, "controller classifier activation")
    return text.encode("utf-8")


def patched_inventory_test() -> bytes:
    relative = "tests/test_mysql_tpch_inventory_retry_support_v1.py"
    text = (OLD_SUPPORT / relative).read_text(encoding="utf-8")
    old = '    monkeypatch.setattr(MODULE, "_classify_two_verified_daemons", lambda result, *a: result)\n'
    new = old + '    monkeypatch.setattr(MODULE, "_classify_verified_local_controller_ancestors", lambda result: result)\n'
    return once(text, old, new, "inventory fixture controller classifier").encode("utf-8")


SUPPORT_TEST = r'''#!/usr/bin/env python3
import importlib.util,json,pathlib,time
import pytest

B=pathlib.Path(__file__).resolve().parents[1]
P=B/"qualification/mysql_distributed_live_readback_qualification_v1.py"
S=importlib.util.spec_from_file_location("phase_prime_core",P)
M=importlib.util.module_from_spec(S); S.loader.exec_module(M)

def row(attempt,sequence,epoch):
 return {"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":attempt,"sequence":sequence,"epoch_seconds":epoch,"completed_total":sequence,"query_duration_ms":1.0,"native_adapter_kind":"sql_query_native","native_marker_epoch_ms":int(round(epoch*1000))}

def test_real_exact_consumer_has_seq0_and_seq1_before_epoch(tmp_path,monkeypatch):
 attempt="tpch-v2-phase-prime-test-123456789abcdef"; metric=tmp_path/"metric.jsonl"; now=time.time()
 metric.write_text(json.dumps(row(attempt,0,now-1.0),sort_keys=True)+"\n")
 class Adapter:
  class WorkloadMetricAdapterError(RuntimeError): pass
  @staticmethod
  def emit_fresh_sample(kind,stdout,path,attempt_id,sequence,previous,timeout_seconds):
   assert kind=="sql_query_native" and sequence==1 and timeout_seconds==2.0
   epoch=time.time(); path.open("a").write(json.dumps(row(attempt_id,1,epoch),sort_keys=True)+"\n"); return int(round(epoch*1000))
 monkeypatch.setattr(M,"metric_adapter",Adapter)
 previous=int(round((now-1.0)*1000))
 assert M._prime_native_metric_one_sample_ahead("sql_query_native",tmp_path/"stdout",metric,attempt,previous)>previous
 zero=M.workload_metric_sample(metric,attempt,None,consume_exact_sequence=True)
 one=M.workload_metric_sample(metric,attempt,0,consume_exact_sequence=True)
 assert [zero["sequence"],one["sequence"]]==[0,1]
 with pytest.raises(M.QualificationError,match="contiguous suffix is absent"):
  M.workload_metric_sample(metric,attempt,1,timeout_seconds=.001,consume_exact_sequence=True)

def test_no_freshness_or_timeout_relaxation():
 source=P.read_text()
 assert "MAXIMUM_SAMPLE_GAP_SECONDS = 5.0" in source
 assert "WORKLOAD_SAMPLE_TIMEOUT_SECONDS = 1.0" in source
 helper=source.split("def _prime_native_metric_one_sample_ahead",1)[1].split("def run_qualification",1)[0]
 assert "timeout_seconds=2.0" in helper
 assert "sequence = 2" in source
 assert '"freshness_threshold_relaxed": False' in source
 assert '"timeout_threshold_relaxed": False' in source
'''.encode("utf-8")


CONTROLLER_TEST = r'''#!/usr/bin/env python3
import hashlib,importlib.util,pathlib

B=pathlib.Path(__file__).resolve().parents[1]
P=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
S=importlib.util.spec_from_file_location("controller_lineage_entry",P)
M=importlib.util.module_from_spec(S); S.loader.exec_module(M)

def test_only_sealed_direct_retry5_ancestor_is_reclassified(tmp_path,monkeypatch):
 attempt="tpch-v2-controller-lineage-test-123456789abc"
 plan_path=tmp_path/"retry5.plan.json"
 plan={"schema_version":"mysql_tpch_retry5_systemd219_plan_v1","qualification_attempt_id":attempt,"collector_bundle":str(B),"launch_argv":[str(P.resolve())],"accepted_round_authorized":False}
 payload=M.core.canonical(plan); plan_path.write_bytes(payload)
 plan_path.with_name(plan_path.name+".sha256").write_text(hashlib.sha256(payload).hexdigest()+"  "+plan_path.name+"\n")
 rows=[
  {"pid":200,"ppid":100,"cmdline_sha256":"a","container_identity":None,"declared_device_writable_fds":0,"role":"qualification_control_plane"},
  {"pid":100,"ppid":1,"cmdline_sha256":"b","container_identity":None,"declared_device_writable_fds":0,"role":"co_resident"},
  {"pid":300,"ppid":1,"cmdline_sha256":"c","container_identity":None,"declared_device_writable_fds":0,"role":"co_resident"},
 ]
 monkeypatch.setattr(M.os,"getpid",lambda:200)
 monkeypatch.setattr(M,"_sealed_collector_command",lambda argv:{"qualification_attempt_id":attempt})
 def argv(pid,digest):
  if pid==200:return ["python",str(P),"--command-spec","unused"]
  if pid==100:return ["python","/sealed/mysql_tpch_retry5_systemd219_completion_v1.py","execute","--plan",str(plan_path)]
  raise AssertionError("foreign process must not be inspected or reclassified")
 monkeypatch.setattr(M,"_proc_argv_and_hash",argv)
 result=M._classify_verified_local_controller_ancestors({"processes":rows})
 assert result["verified_local_controller_ancestor_pids"]==[100]
 assert rows[1]["role"]=="qualification_control_plane"
 assert rows[2]["role"]=="co_resident"
'''.encode("utf-8")


def build_support() -> tuple[Path, str, str]:
    if sha((OLD_SUPPORT / "bundle_manifest.json").read_bytes()) != OLD_SUPPORT_SHA:
        raise RuntimeError("old support manifest differs")
    files = copied_files(OLD_SUPPORT, {
        "bundle_manifest.json", "bundle_manifest.json.sha256", CORE, ENTRY,
        "tests/test_mysql_tpch_a81_native_metric_phase_prime_v1.py",
        "tests/test_mysql_tpch_a81_controller_lineage_v1.py",
        "tests/test_mysql_tpch_inventory_retry_support_v1.py",
    })
    files[CORE] = patched_core()
    files[ENTRY] = patched_entry()
    files["tests/test_mysql_tpch_a81_native_metric_phase_prime_v1.py"] = SUPPORT_TEST
    files["tests/test_mysql_tpch_a81_controller_lineage_v1.py"] = CONTROLLER_TEST
    files["tests/test_mysql_tpch_inventory_retry_support_v1.py"] = patched_inventory_test()
    root_cause = canonical({
        "schema_version": "mysql_tpch_a81_native_metric_phase_race_root_cause_v1",
        "status": "PASS_OFFLINE_ROOT_CAUSE_BOUND_ZERO_CREDIT",
        "failed_attempt": FAILED_ATTEMPT,
        "observed_native_sequences": {"first": 0, "last": 100, "count": 101},
        "observed_error": "workload metric contiguous suffix is absent",
        "native_command_duration_seconds": 240,
        "predecessor_support_bundle_id": OLD_SUPPORT_ID,
        "root_cause": "scheduled 250ms producer lead did not establish ordering against independent native 1Hz phase",
        "remediation": "strictly emit sequence1 before collector epoch then emit sequences2..180",
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "co_resident_conflict_relaxed": False,
        "co_resident_process_identity_reconciliation_required": True,
        "own_retry5_controller_lineage_exactly_reclassified": True,
        "foreign_process_exclusion_prohibited": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    files["authority/mysql_tpch_a81_native_metric_phase_race_root_cause_v1.json"] = root_cause
    old = json.loads((OLD_SUPPORT / "bundle_manifest.json").read_text())
    identity = {key: value for key, value in old.items()
                if key not in ("bundle_id", "files", "schema_version", "status",
                               "predecessor_bundle_id", "predecessor_manifest_sha256")}
    identity.update({
        "schema_version": "mysql_tpch_native_metric_phase_prime_successor_bundle_v1",
        "status": "SEALED_LOCAL_A81_PHASE_PRIME_NOT_EXECUTED",
        "predecessor_bundle_id": OLD_SUPPORT_ID,
        "predecessor_manifest_sha256": OLD_SUPPORT_SHA,
        "failed_attempt_token": FAILED_ATTEMPT,
        "failed_attempt_rerun_prohibited": True,
        "native_metric_phase_prime_sequence": 1,
        "native_metric_producer_initial_sequence": 2,
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "co_resident_conflict_relaxed": False,
        "invalidated_local_support_bundle_id":
            "ed02e123038078d38246ad6cae4eab5afac637b99fadcec35af1974218275266",
        "invalidated_local_support_reason":
            "inherited inventory test fixture lacked the new exact classifier stub; never published or executed",
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    bundle_id, manifest = manifest_bytes(identity, files)
    path = materialize(OUT / "support", bundle_id, manifest, files, OLD_SUPPORT)
    return path, bundle_id, sha(manifest)


def patched_controller(support_id: str, support_sha: str) -> bytes:
    text = (BASE / CONTROLLER).read_text(encoding="utf-8")
    text = once(text, f'SUPPORT_ID = "{OLD_SUPPORT_ID}"',
                f'SUPPORT_ID = "{support_id}"', "support ID")
    text = once(text, f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"',
                f'SUPPORT_SHA = "{support_sha}"', "support manifest SHA")
    old = (f'ROOT_SYSTEMD_ATTEMPT = "{FAILED_ATTEMPT}"\n'
           f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED_ATTEMPT}.service"\n'
           f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry9_root_oneclick_" + FAILED_ATTEMPT)}")')
    new = (f'FAILED_A81_ATTEMPT = "{FAILED_ATTEMPT}"\n'
           f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"\n'
           f'ROOT_SYSTEMD_UNIT = "{UNIT}"\n'
           f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")')
    text = once(text, old, new, "fresh retry10 identity")
    text = once(text,
        '        "failure_systemd_auth": root / "evidence/mysql_tpch_retry9_a80_systemd_interactive_auth_failure_exact_v1.json",\n',
        '        "failure_systemd_auth": root / "evidence/mysql_tpch_retry9_a80_systemd_interactive_auth_failure_exact_v1.json",\n'
        f'        "failure_a81": root / "{FAILURE}",\n', "a81 evidence path")
    text = once(text,
        '    if attempt == FAILED_SYSTEMD_AUTH_ATTEMPT:\n        raise OneClickError("failed Retry9 a80 systemd-authorization attempt rerun is prohibited")\n',
        '    if attempt == FAILED_SYSTEMD_AUTH_ATTEMPT:\n        raise OneClickError("failed Retry9 a80 systemd-authorization attempt rerun is prohibited")\n'
        '    if attempt == FAILED_A81_ATTEMPT:\n        raise OneClickError("failed a81 native-metric attempt rerun is prohibited")\n', "a81 materialize guard")
    text = once(text,
        '        "failed_systemd_auth_evidence": str(paths["failure_systemd_auth"]),\n',
        '        "failed_systemd_auth_evidence": str(paths["failure_systemd_auth"]),\n'
        '        "failed_a81_attempt": FAILED_A81_ATTEMPT,\n'
        '        "failed_a81_evidence": str(paths["failure_a81"]),\n', "a81 plan binding")
    text = once(text,
        '            or plan.get("attempt_token") == FAILED_SYSTEMD_AUTH_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',
        '            or plan.get("attempt_token") == FAILED_SYSTEMD_AUTH_ATTEMPT\n'
        '            or plan.get("failed_a81_attempt") != FAILED_A81_ATTEMPT\n'
        '            or plan.get("attempt_token") == FAILED_A81_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n', "a81 execute guard")
    return text.encode("utf-8")


OUTER_TEST = r'''#!/usr/bin/env python3
import hashlib,json,pathlib,stat,subprocess,sys
B=pathlib.Path(sys.argv[1]); m=json.loads((B/"bundle_manifest.json").read_text()); c=(B/"controller/mysql_tpch_retry9_one_click_v1.py").read_text()
compile(c,str(B/"controller/mysql_tpch_retry9_one_click_v1.py"),"exec")
for p in (B/"launch").glob("*.sh"): subprocess.run(["bash","-n",str(p)],check=True)
sid=m["support_collector_bundle_id"]; support=B/"dependencies/support"/sid; sm=json.loads((support/"bundle_manifest.json").read_text())
assert sm["native_metric_phase_prime_sequence"]==1 and sm["native_metric_producer_initial_sequence"]==2
assert sm["freshness_threshold_relaxed"] is False and sm["timeout_threshold_relaxed"] is False and sm["co_resident_conflict_relaxed"] is False
assert 'failed a81 native-metric attempt rerun is prohibited' in c
assert "--property=CPUAffinity=16 17 52 53" in (B/"launch/root_start_and_wait_retry9_systemd.sh").read_text()
assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["accepted_rounds_credit"]==0
for p in B.rglob("*"):
 assert not p.is_symlink()
 if p.is_file(): assert not stat.S_IMODE(p.stat().st_mode)&0o222
print("PASS_OFFLINE_A81_PHASE_PRIME_SUCCESSOR")
'''.encode("utf-8")


ROOT_COMPAT_TEST = r'''#!/usr/bin/env python3
import json,pathlib,stat,subprocess,sys
B=pathlib.Path(sys.argv[1]); C=B/"controller/mysql_tpch_retry9_one_click_v1.py"; R=B/"launch/root_start_and_wait_retry9_systemd.sh"; I=B/"launch/run_retry9_root_systemd_principal.sh"
for p in (R,I): subprocess.run(["bash","-n",str(p)],check=True)
s=C.read_text(); r=R.read_text(); i=I.read_text(); f=json.loads((B/"evidence/mysql_tpch_retry9_d247_root_lock_principal_failure_exact_v1.json").read_text()); m=json.loads((B/"bundle_manifest.json").read_text())
assert f["pids"]=={"bootstrap":None,"controller":None,"workload":None} and f["accepted_rounds_credit"]==0
assert 'failed Retry9 d247 root-lock attempt rerun is prohibited' in s and 'root systemd principal receipt differs' in s
assert '--property=User=root --property=Group=root' in r and '--property=CPUAffinity=16 17 52 53' in r
assert 'root-run --attempt-token tpch-v2-20260803t124500z-b235retry10f6c0a81e4d2b' in i
assert m["predecessor_bundle_id"]=="042961efb0d52bf29e8410546d4ff3c6122b54b7fcdad11db53b64cfe6b57dbd" and m["d3d_tmpfs_fix_preserved"] is True
for p in B.rglob("*"):
 assert not p.is_symlink()
 if p.is_file(): assert not stat.S_IMODE(p.stat().st_mode)&0o222
print("PASS_OFFLINE_RETRY10_ROOT_SYSTEMD_COMPAT")
'''.encode("utf-8")


SUDO_COMPAT_TEST = r'''#!/usr/bin/env python3
import json,pathlib,stat,subprocess,sys
B=pathlib.Path(sys.argv[1]); C=B/"controller/mysql_tpch_retry9_one_click_v1.py"; O=B/"launch/launch_and_wait_retry9_root_systemd.sh"; R=B/"launch/root_start_and_wait_retry9_systemd.sh"; I=B/"launch/run_retry9_root_systemd_principal.sh"
for p in (O,R,I): subprocess.run(["bash","-n",str(p)],check=True)
s=C.read_text(); o=O.read_text(); r=R.read_text(); i=I.read_text(); m=json.loads((B/"bundle_manifest.json").read_text())
assert o.count('exec /usr/bin/sudo -n /bin/sh "$ROOT_STARTER"')==1
assert '--property=User=root --property=Group=root' in r and '--property=CPUAffinity=16 17 52 53' in r
assert 'root-run --attempt-token tpch-v2-20260803t124500z-b235retry10f6c0a81e4d2b' in i
assert 'failed Retry9 a80 systemd-authorization attempt rerun is prohibited' in s and 'failed a81 native-metric attempt rerun is prohibited' in s
sid=m["support_collector_bundle_id"]; assert (B/"dependencies/support"/sid/"bundle_manifest.json").is_file()
assert m["sudo_path"]=="/usr/bin/sudo" and m["sudo_mode"]=="-n" and m["accepted_rounds_credit"]==0
for p in B.rglob("*"):
 assert not p.is_symlink()
 if p.is_file(): assert not stat.S_IMODE(p.stat().st_mode)&0o222
print("PASS_OFFLINE_RETRY10_SUDO_N_COMPAT")
'''.encode("utf-8")


def build_outer(support_path: Path, support_id: str, support_sha: str) -> Path:
    if sha((BASE / "bundle_manifest.json").read_bytes()) != A81_SHA:
        raise RuntimeError("a81 manifest differs")
    old_prefix = "dependencies/support/" + OLD_SUPPORT_ID + "/"
    files = copied_files(BASE, {
        "bundle_manifest.json", "bundle_manifest.json.sha256", CONTROLLER,
        "launch/root_start_and_wait_retry9_systemd.sh",
        "launch/run_retry9_root_systemd_principal.sh",
        "templates/node04_one_click_command.json",
        "tests/test_mysql_tpch_retry9_root_systemd_principal_successor_v1.py",
        "tests/test_mysql_tpch_retry9_sudo_n_root_systemd_successor_v1.py",
    })
    files = {name: data for name, data in files.items()
             if not name.startswith(old_prefix)}
    for path in support_path.rglob("*"):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(support_path).as_posix()
            files[f"dependencies/support/{support_id}/{relative}"] = path.read_bytes()
    files[CONTROLLER] = patched_controller(support_id, support_sha)
    inner = f'''#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\nSELF=$(readlink -f -- "$0")\nBUNDLE=$(cd -- "$(dirname -- "$SELF")/.." && pwd -P)\nPY={RUNTIME}/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11\nexec "$PY" -B "$BUNDLE/{CONTROLLER}" root-run --attempt-token {ATTEMPT} --expected-unit {UNIT} --output-dir {OUTPUT}\n'''.encode()
    starter = (BASE / "launch/root_start_and_wait_retry9_systemd.sh").read_text()
    starter = starter.replace("ra-mysql-tpch-oneclick-root-" + FAILED_ATTEMPT + ".service", UNIT)
    starter = starter.replace(FAILED_ATTEMPT, ATTEMPT)
    starter = starter.replace("retry9_root_oneclick_" + ATTEMPT, "retry10_root_oneclick_" + ATTEMPT)
    starter = once(
        starter,
        '--property=User=root --property=Group=root /bin/sh "$INNER"',
        '--property=User=root --property=Group=root '
        "'--property=CPUAffinity=16 17 52 53' /bin/sh \"$INNER\"",
        "root controller CPU affinity",
    )
    files["launch/root_start_and_wait_retry9_systemd.sh"] = starter.encode()
    files["launch/run_retry9_root_systemd_principal.sh"] = inner
    template = json.loads((BASE / "templates/node04_one_click_command.json").read_text())
    template.update({"schema_version": "mysql_tpch_retry10_phase_prime_node04_command_v1",
                     "attempt_token": ATTEMPT, "systemd_unit": UNIT,
                     "failed_a81_attempt_rerun_prohibited": True,
                     "support_collector_bundle_id": support_id,
                     "support_collector_manifest_sha256": support_sha})
    files["templates/node04_one_click_command.json"] = canonical(template)
    failure = canonical({
        "schema_version": "mysql_tpch_a81_seq100_failure_zero_credit_v1",
        "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token": FAILED_ATTEMPT,
        "predecessor_bundle_id": A81_ID,
        "predecessor_manifest_sha256": A81_SHA,
        "support_collector_bundle_id": OLD_SUPPORT_ID,
        "native_sequence_first": 0, "native_sequence_last": 100,
        "error": "workload metric contiguous suffix is absent",
        "command_duration_seconds": 240,
        "rolling_inventory_conflict_observed": "co_resident_cpu_or_device",
        "rolling_inventory_conflict_relaxed": False,
        "rerun_prohibited": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
    })
    files[FAILURE] = failure
    files[FAILURE + ".sha256"] = (sha(failure) + "  " + Path(FAILURE).name + "\n").encode()
    files["tests/test_mysql_tpch_a81_phase_prime_successor_v1.py"] = OUTER_TEST
    files["tests/test_mysql_tpch_retry9_root_systemd_principal_successor_v1.py"] = ROOT_COMPAT_TEST
    files["tests/test_mysql_tpch_retry9_sudo_n_root_systemd_successor_v1.py"] = SUDO_COMPAT_TEST
    files["builder/build_mysql_tpch_a81_phase_prime_successor_v1.py"] = Path(__file__).read_bytes()
    old = json.loads((BASE / "bundle_manifest.json").read_text())
    identity = {key: value for key, value in old.items()
                if key not in ("bundle_id", "files", "schema_version", "status",
                               "predecessor_bundle_id", "predecessor_manifest_sha256",
                               "new_attempt_token", "root_systemd_unit")}
    identity.update({
        "schema_version": "mysql_tpch_a81_phase_prime_successor_bundle_v1",
        "status": "SEALED_LOCAL_A81_PHASE_PRIME_SUCCESSOR_NOT_EXECUTED",
        "predecessor_bundle_id": A81_ID,
        "predecessor_manifest_sha256": A81_SHA,
        "failed_a81_attempt_token": FAILED_ATTEMPT,
        "failed_a81_rerun_prohibited": True,
        "support_collector_bundle_id": support_id,
        "support_collector_manifest_sha256": support_sha,
        "new_attempt_token": ATTEMPT,
        "root_systemd_unit": UNIT,
        "native_metric_phase_prime_sequence": 1,
        "native_metric_producer_initial_sequence": 2,
        "freshness_threshold_relaxed": False,
        "timeout_threshold_relaxed": False,
        "co_resident_conflict_relaxed": False,
        "co_resident_process_identity_reconciliation_required": True,
        "own_retry5_controller_lineage_exactly_reclassified": True,
        "root_controller_cpuset_cpus": "16-17,52-53",
        "root_controller_cpu_disjoint_from_tpch_mysql_and_client": True,
        "foreign_process_exclusion_prohibited": True,
        "invalidated_local_outer_bundle_id":
            "fcf4c36fa105a2406109925027498eca16082cac2810893f7a5549271e06aefc",
        "invalidated_local_outer_reason":
            "outer inherited compatibility scripts still asserted a81 identity; never published or executed",
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    bundle_id, manifest = manifest_bytes(identity, files)
    return materialize(OUT, bundle_id, manifest, files, BASE)


def main() -> None:
    support_path, support_id, support_sha = build_support()
    outer = build_outer(support_path, support_id, support_sha)
    print(json.dumps({
        "bundle": str(outer), "bundle_id": outer.name,
        "manifest_sha256": sha((outer / "bundle_manifest.json").read_bytes()),
        "support_bundle_id": support_id,
        "support_manifest_sha256": support_sha,
        "attempt_token": ATTEMPT,
        "unique_launch_argv": ["/bin/sh", str(outer / "launch/launch_and_wait_retry9_root_systemd.sh")],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
