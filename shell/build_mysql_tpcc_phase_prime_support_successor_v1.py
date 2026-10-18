#!/usr/bin/env python3
"""Build TPC-C-specific da455 phase-prime/exact-consumer support."""
from __future__ import annotations
import hashlib,importlib.util,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
PARENT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";PARENT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"
PARENT=ROOT/"artifacts/mysql_tpch_native_metric_completion_successor_v1"/PARENT_ID;VERIFIED=ROOT/"artifacts/mysql_tpch_a81_phase_prime_successor_v1/support/735a049e61551796c78dd5fbdd6f4bd19b07d39e176f040d1c59c6577a65bb41";OUT=ROOT/"artifacts/mysql_tpcc_phase_prime_support_successor_v1"
CORE="qualification/mysql_distributed_live_readback_qualification_v1.py";ENTRY="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def once(text,old,new,label):
 if text.count(old)!=1:raise BuildError(label+" anchor differs="+str(text.count(old)))
 return text.replace(old,new,1)
def module(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def patch_core(raw):
 pacing=module(VERIFIED/"authority/build_mysql_tpch_native_metric_pacing_successor_v1.py","tpcc_pacing_builder")
 exact=module(VERIFIED/"authority/build_mysql_tpch_native_metric_exact_consumer_successor_v1.py","tpcc_exact_builder")
 raw=exact.patch_core(pacing.patch_core(raw));text=raw.decode()
 helper='''\n\ndef _prime_native_metric_one_sample_ahead(\n    native_adapter_kind, stdout_path, metric_path, attempt_id,\n    previous_native_epoch_ms,\n):\n    """Seal seq1 before the collector epoch under the frozen strict timeout."""\n    return metric_adapter.emit_fresh_sample(\n        native_adapter_kind, stdout_path, metric_path, attempt_id, 1,\n        previous_native_epoch_ms, timeout_seconds=2.0,\n    )\n'''
 text=once(text,"\ndef run_qualification(\n",helper+"\n\ndef run_qualification(\n","prime helper")
 old='''            # Bind both emission and collection to one monotonic epoch.
            # Sequence zero was emitted synchronously above.  The remaining
            # exact suffix is emitted at 1 Hz with a 250 ms producer lead; this
            # prevents a fast native query stream from exhausting seq180 before
            # the final collector tick without permitting stale repeats.
            started = time.monotonic()
            def produce_native_metrics():
                sequence = 1
'''
 new='''            # Prime seq1 under the frozen strict timeout before the epoch.
            try:
                native_previous_epoch_ms = _prime_native_metric_one_sample_ahead(
                    native_adapter_kind, stdout_path, metric_path,
                    spec["qualification_attempt_id"], native_previous_epoch_ms,
                )
            except metric_adapter.WorkloadMetricAdapterError as exc:
                raise QualificationError("native metric phase prime failed: %s" % exc)
            started = time.monotonic()
            def produce_native_metrics():
                sequence = 2
'''
 return once(text,old,new,"prime sequence").encode()

CLI_HELPER=r'''

def _python_script_index(argv, expected_script):
    if not isinstance(argv, list) or len(argv) < 3:
        raise core.QualificationError("collector Python CLI identity differs")
    try:
        if Path(argv[0]).resolve() != Path(sys.executable).resolve():
            raise core.QualificationError("collector Python executable differs")
    except OSError as exc:
        raise core.QualificationError("collector Python executable differs") from exc
    index = 2 if argv[1:2] == ["-B"] else 1
    if index >= len(argv) or Path(argv[index]).resolve() != Path(expected_script).resolve():
        raise core.QualificationError("collector Python script identity differs")
    if "-B" in argv and index != 2:
        raise core.QualificationError("collector Python -B position differs")
    return index
'''

CONTROLLER_HELPER=r'''

def _classify_verified_tpcc_controller(result):
    rows = result.get("processes") or []
    by_pid = {int(row.get("pid") or 0): row for row in rows}
    own = by_pid.get(os.getpid())
    if own is None:
        raise core.QualificationError("own TPC-C collector row is absent")
    own_argv = _proc_argv_and_hash(os.getpid(), own.get("cmdline_sha256"))
    command = _sealed_collector_command(own_argv)
    parent = by_pid.get(int(own.get("ppid") or 0))
    if parent is None:
        raise core.QualificationError("TPC-C controller parent row is absent")
    argv = _proc_argv_and_hash(int(parent["pid"]), parent.get("cmdline_sha256"))
    candidates = [value for value in argv if Path(value).name == "mysql_tpcc_v16_independent_parallel_one_click_v1.py"]
    if len(candidates) != 1:
        raise core.QualificationError("TPC-C controller executable identity differs")
    script = Path(candidates[0]).resolve(strict=True)
    _python_script_index(argv, script)
    if argv.count("execute") != 1:
        raise core.QualificationError("TPC-C controller execute mode differs")
    root = script.parents[1]; manifest_path = root / "bundle_manifest.json"
    manifest = core.load_object(manifest_path, "TPC-C controller manifest")
    relative = script.relative_to(root).as_posix()
    if (not core.valid_sidecar(manifest_path)
            or manifest.get("bundle_id") != root.name
            or (manifest.get("files") or {}).get(relative) != core.sha256(script)
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("accepted_round_authorized") is not False):
        raise core.QualificationError("TPC-C controller bundle identity differs")
    plan_path = Path(_exact_option(argv, "--plan")); authority_path = Path(_exact_option(argv, "--authority")); token_path = Path(_exact_option(argv, "--token"))
    plan = core.load_object(plan_path, "TPC-C controller plan"); authority = core.load_object(authority_path, "TPC-C controller authority"); token = token_path.read_text(encoding="ascii").strip(); q = plan.get("qualification_args") or []
    mode_flags = [flag for flag in ("--qualification-solo", "--qualification-paired") if flag in own_argv]
    if len(mode_flags) != 1:
        raise core.QualificationError("TPC-C collector qualification mode differs")
    own_tail = own_argv[own_argv.index(mode_flags[0]) + 1:]
    if "--output" not in own_tail:
        raise core.QualificationError("TPC-C collector output CLI differs")
    own_q = own_tail[:own_tail.index("--output")]
    if (not core.valid_sidecar(plan_path) or plan.get("collector_bundle") != str(HERE.parent)
            or q != own_q or plan.get("formal_scope") != "mysql_only"
            or plan.get("accepted_round_authorized") is not False
            or not core.valid_sidecar(authority_path)
            or authority.get("plan_sha256") != core.sha256(plan_path)
            or authority.get("token_sha256") != core.sha_bytes(token.encode("ascii"))
            or not float(authority.get("not_before_epoch") or 0) <= time.time() < float(authority.get("expires_epoch") or 0)):
        raise core.QualificationError("TPC-C controller plan/authority identity differs")
    if (parent.get("container_identity") is not None
            or int(parent.get("declared_device_writable_fds") or 0) != 0):
        raise core.QualificationError("TPC-C controller isolation differs")
    parent["role"] = "qualification_control_plane"; parent["controller_lineage_authority"] = "sealed_tpcc_controller_bundle_plan_authority_and_exact_cli"
    result["verified_local_controller_ancestor_pids"] = [int(parent["pid"])]
    result["local_controller_lineage_exact_authority_verified"] = True
    return result
'''

def patch_entry(raw):
 text=raw.decode();text=once(text,"\ndef _sealed_collector_command(argv):\n",CLI_HELPER+"\n\ndef _sealed_collector_command(argv):\n","CLI helper")
 old='''    entry = str(Path(__file__).resolve())
    if (len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve()
            or Path(argv[1]).resolve() != Path(entry)
            or argv.count("--qualification-paired") != 1):
        raise core.QualificationError("paired collector executable identity differs")
'''
 new='''    entry = str(Path(__file__).resolve())
    script_index = _python_script_index(argv, entry)
    solo = argv.count("--qualification-solo")
    paired = argv.count("--qualification-paired")
    if (solo + paired != 1 or script_index + 1 >= len(argv)
            or argv[script_index + 1] not in ("--qualification-solo", "--qualification-paired")):
        raise core.QualificationError("TPC-C collector executable/mode identity differs")
    expected_mode = "qualification_solo" if solo else "qualification_paired"
'''
 text=once(text,old,new,"collector executable")
 text=once(text,'or command.get("mode") != "qualification_paired" or command.get("debug_only") is not True','or command.get("mode") != expected_mode or command.get("debug_only") is not True',"command mode")
 text=once(text,"\ndef _numeric_proc_pid_set():\n",CONTROLLER_HELPER+"\n\ndef _numeric_proc_pid_set():\n","controller helper")
 anchor='''        result = _mark_pid_set_consistency(result, before, after)
        result = _classify_two_verified_daemons(
'''
 replacement='''        result = _mark_pid_set_consistency(result, before, after)
        result = _classify_verified_tpcc_controller(result)
        result = _classify_two_verified_daemons(
'''
 return once(text,anchor,replacement,"controller invocation").encode()

TEST=r'''import hashlib,importlib.util,json,os,sys,time
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];E=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";C=B/"qualification/mysql_distributed_live_readback_qualification_v1.py"
def load(path,name):s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def row(a,n,e):return {"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":a,"sequence":n,"epoch_seconds":e,"completed_total":n,"query_duration_ms":1.0,"native_adapter_kind":"sql_query_native","native_marker_epoch_ms":int(e*1000)}
def test_content_address():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name
def test_real_tpcc_qualification_cli_identity_accepts_B_solo_and_rejects_wrong_script(monkeypatch,tmp_path):
 m=load(E,"tpcc_entry_cli");monkeypatch.setattr(m.sys,"executable",sys.executable);entry=str(E.resolve());real=[sys.executable,"-B",entry,"--qualification-solo"];assert m._python_script_index(real,entry)==2
 with pytest.raises(m.core.QualificationError,match="collector control option differs: --command-spec"):m._sealed_collector_command(real)
 paired=[sys.executable,"-B",entry,"--qualification-paired"];assert m._python_script_index(paired,entry)==2
 with pytest.raises(m.core.QualificationError,match="collector control option differs: --command-spec"):m._sealed_collector_command(paired)
 with pytest.raises(m.core.QualificationError):m._python_script_index([sys.executable,"-B",entry+".wrong","--qualification-solo"],entry)
 source=E.read_text();assert 'expected_mode = "qualification_solo" if solo else "qualification_paired"' in source and 'mysql_tpch_retry5_systemd219_completion_v1.py' not in source and 'mysql_tpcc_v16_independent_parallel_one_click_v1.py' in source
def test_seq0_to_180_exact(tmp_path):
 m=load(C,"tpcc_core_seq");a="tpcc-cli-"+"a"*40;e=time.time();p=tmp_path/"m.jsonl";p.write_text("".join(json.dumps(row(a,n,e))+"\n" for n in range(181)));prev=None
 for n in range(181):v=m.workload_metric_sample(p,a,prev,consume_exact_sequence=True);assert v["sequence"]==n;prev=n
def test_frozen_thresholds_and_transport():
 s=C.read_text();assert "MAXIMUM_SAMPLE_GAP_SECONDS = 5.0" in s and "WORKLOAD_SAMPLE_TIMEOUT_SECONDS = 1.0" in s and "timeout_seconds=2.0" in s and "sequence = 2" in s
 assert hashlib.sha256((B/"qualification/mysql_shared_tpcc_counter_transport_v1.py").read_bytes()).hexdigest()=="f0462fefbf79630d8617756b0363d43de1341c7498ebd871b3352e4a0e5359ea"
'''
def verify_parent():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA or hs(canonical(u))!=PARENT_ID or m.get("bundle_id")!=PARENT_ID:raise BuildError("da455 parent differs")
 for rel,want in (m.get("files") or {}).items():
  if sha(PARENT/rel)!=want:raise BuildError("da455 closure differs "+rel)
 return m
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)
def main():
 pm=verify_parent();content={}
 for rel in sorted(pm["files"]):
  if not rel.startswith("tests/"):content[rel]=(PARENT/rel).read_bytes()
 content[CORE]=patch_core(content[CORE]);content[ENTRY]=patch_entry(content[ENTRY]);content["tests/test_mysql_tpcc_phase_prime_support_successor_v1.py"]=TEST.encode();content["builder/build_mysql_tpcc_phase_prime_support_successor_v1.py"]=HERE.read_bytes()
 audit={"schema_version":"mysql_tpcc_dba3_paired_executable_identity_root_cause_v1","failed_error":"paired collector executable identity differs","root_cause":"735a self-controller validator hard-coded python-without-B paired TPC-H CLI","fix":"da455 TPC-C transport plus exact python -B solo CLI and sealed TPC-C controller lineage, with only verified phase prime/exact consumer metric changes","failed_credit":0};content["authority/mysql_tpcc_dba3_paired_executable_identity_root_cause_v1.json"]=canonical(audit)
 files={k:hs(v) for k,v in sorted(content.items())};u={"schema_version":"mysql_tpcc_phase_prime_support_successor_bundle_v1","status":"SEALED_TPCC_EXACT_CLI_PHASE_PRIME_SUPPORT_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"failed_dba3_error":"paired collector executable identity differs","tpcc_transport_sha256":"f0462fefbf79630d8617756b0363d43de1341c7498ebd871b3352e4a0e5359ea","tpcc_python_B_qualification_solo_cli_required":True,"tpch_retry_controller_constants_prohibited":True,"native_metric_phase_prime_sequence":1,"native_metric_producer_initial_sequence":2,"exact_consumer_first_sequence":0,"exact_consumer_last_sequence":180,"minimum_sample_count":181,"freshness_threshold_relaxed":False,"timeout_threshold_relaxed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files};bid=hs(canonical(u));m=dict(u,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/bid
 if dst.exists():print(bid);print(sha(dst/"bundle_manifest.json"));return
 st=Path(tempfile.mkdtemp(prefix=".tpcc-phase-",dir=OUT))
 try:
  for rel,payload in content.items():p=st/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(st);os.rename(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 print(bid);print(sha(dst/"bundle_manifest.json"));print(dst)
if __name__=="__main__":main()
