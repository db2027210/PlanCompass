#!/usr/bin/env python3
"""Build the immutable ff313 post-qualification R0/Top5 successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OUT = ROOT / "artifacts/mysql_tpch_postqualification_r0_top5_successor_v1"
TARGET = ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1/ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
SUPPORT = ROOT / "artifacts/mysql_tpch_inventory_retry_support_v1/86232f80b4e274aadf1cdbd8acc19033786acfda8dafb6dc18f7bace4bdb61a3"
TARGET_ID = TARGET.name
TARGET_MANIFEST_SHA = "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
SUPPORT_ID = SUPPORT.name
SUPPORT_MANIFEST_SHA = "2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7"
SUPPORT_ENTRY_SHA = "5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1"
SUPPORT_CORE_SHA = "6cc0958fe0c485f0b5efa494b4e9b4868488a4fad07f06e4b11c7978f9f2594f"
ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
DB = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"
CLIENT = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def patch_r0() -> bytes:
    path = TARGET / "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from pathlib import Path\n",
        "from pathlib import Path\nfrom datetime import datetime, timezone\n",
        1,
    )
    marker = "HEX64 = re.compile(r\"[0-9a-f]{64}$\")\n"
    constants = marker + f'''TARGET_BUNDLE_ID = "{TARGET_ID}"
TARGET_MANIFEST_SHA256 = "{TARGET_MANIFEST_SHA}"
SUPPORT_BUNDLE_ID = "{SUPPORT_ID}"
SUPPORT_MANIFEST_SHA256 = "{SUPPORT_MANIFEST_SHA}"
SUPPORT_ENTRY_SHA256 = "{SUPPORT_ENTRY_SHA}"
SUPPORT_CORE_SHA256 = "{SUPPORT_CORE_SHA}"
TARGET_ATTEMPT = "{ATTEMPT}"
TARGET_DATABASE_CONTAINER = "{DB}"
TARGET_CLIENT_CONTAINER = "{CLIENT}"
MAX_GATE_LIFETIME_SECONDS = 900
'''
    if text.count(marker) != 1:
        raise RuntimeError("R0 constants marker differs")
    text = text.replace(marker, constants, 1)
    marker = "def read_token(path: Path) -> str:\n"
    helpers = r'''def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise R0Error(f"{label} is absent")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise R0Error(f"{label} differs") from exc
    if parsed.tzinfo is None:
        raise R0Error(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_gate_window(gate: Mapping[str, Any], now: datetime | None = None) -> None:
    captured = parse_utc(gate.get("captured_at_utc"), "gate captured_at_utc")
    expires = parse_utc(gate.get("expires_at_utc"), "gate expires_at_utc")
    effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lifetime = (expires - captured).total_seconds()
    if lifetime <= 0 or lifetime > MAX_GATE_LIFETIME_SECONDS:
        raise R0Error("dynamic gate lifetime differs")
    if effective_now < captured or effective_now >= expires:
        raise R0Error("dynamic gate is not currently valid")


def verify_support_bundle(path: Path) -> Path:
    support = Path(path).resolve(strict=True)
    manifest_path = support / "bundle_manifest.json"
    manifest = load_sealed(manifest_path, "support bundle manifest")
    entry = support / "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
    if (support.name != SUPPORT_BUNDLE_ID
            or sha256(manifest_path) != SUPPORT_MANIFEST_SHA256
            or manifest.get("bundle_id") != SUPPORT_BUNDLE_ID
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or sha256(entry) != SUPPORT_ENTRY_SHA256
            or sha256(support / "qualification/mysql_distributed_live_readback_qualification_v1.py") != SUPPORT_CORE_SHA256):
        raise R0Error("support collector bundle binding differs")
    return entry


def read_token(path: Path) -> str:
'''
    if text.count(marker) != 1:
        raise RuntimeError("R0 token marker differs")
    text = text.replace(marker, helpers, 1)
    text = text.replace(
        "    gate_sha256: str,\n    raw: Mapping[str, Any],",
        "    gate_sha256: str,\n    support_manifest_sha256: str,\n    raw: Mapping[str, Any],",
        1,
    )
    old = '''        or gate.get("serial_fallback_required") is not False
        or raw.get("schema_version")
'''
    new = '''        or gate.get("serial_fallback_required") is not False
        or gate.get("collector_sha256") != SUPPORT_MANIFEST_SHA256
        or raw.get("schema_version")
'''
    if text.count(old) != 1:
        raise RuntimeError("R0 gate collector marker differs")
    text = text.replace(old, new, 1)
    old = '''        or raw.get("hostname") != "node04"
        or raw.get("duration_seconds") != 180
'''
    new = '''        or raw.get("hostname") != "node04"
        or raw.get("qualification_attempt_id") != bootstrap.get("attempt_token")
        or raw.get("collector_sha256") != SUPPORT_MANIFEST_SHA256
        or raw.get("duration_seconds") != 180
'''
    if text.count(old) != 1:
        raise RuntimeError("R0 raw attempt marker differs")
    text = text.replace(old, new, 1)
    text = text.replace(
        '        "dynamic_gate_sha256": gate_sha256,\n',
        '        "dynamic_gate_sha256": gate_sha256,\n'
        '        "support_collector_bundle_id": SUPPORT_BUNDLE_ID,\n'
        '        "support_collector_manifest_sha256": support_manifest_sha256,\n'
        '        "support_collector_entry_sha256": SUPPORT_ENTRY_SHA256,\n',
        1,
    )
    text = text.replace(
        '        "accepted_rounds_credit": 0,\n        "accepted_round_authorized": False,\n',
        '        "qualification_credit": 0,\n'
        '        "accepted_rounds_credit": 0,\n'
        '        "accepted_round_authorized": False,\n'
        '        "formal_launch_authorized": False,\n'
        '        "formal_r0_authorized": False,\n',
        1,
    )
    text = text.replace(
        '    parser.add_argument("--bundle", type=Path, required=True)\n',
        '    parser.add_argument("--bundle", type=Path, required=True)\n'
        '    parser.add_argument("--support-bundle", type=Path, required=True)\n',
        1,
    )
    old = '''    if manifest.get("bundle_id") != bundle.name:
        raise R0Error("bundle identity differs")
    bootstrap = load_sealed(args.bootstrap, "bootstrap")
    gate = load_sealed(args.dynamic_gate, "dynamic gate")
'''
    new = '''    if (bundle.name != TARGET_BUNDLE_ID
            or sha256(bundle / "bundle_manifest.json") != TARGET_MANIFEST_SHA256
            or manifest.get("bundle_id") != bundle.name):
        raise R0Error("bundle identity differs")
    collector = verify_support_bundle(args.support_bundle)
    support_manifest_sha256 = sha256(
        args.support_bundle.resolve(strict=True) / "bundle_manifest.json"
    )
    bootstrap = load_sealed(args.bootstrap, "bootstrap")
    gate = load_sealed(args.dynamic_gate, "dynamic gate")
    validate_gate_window(gate)
'''
    if text.count(old) != 1:
        raise RuntimeError("R0 main bundle marker differs")
    text = text.replace(old, new, 1)
    old = '''        command.get("bundle_id") != bundle.name
        or command.get("mode") != "qualification_solo"
'''
    new = '''        command.get("bundle_id") != bundle.name
        or command.get("qualification_attempt_id") != bootstrap.get("attempt_token")
        or bootstrap.get("attempt_token") != TARGET_ATTEMPT
        or gate.get("attempt_token") != TARGET_ATTEMPT
        or args.database_container != TARGET_DATABASE_CONTAINER
        or args.client_container != TARGET_CLIENT_CONTAINER
        or command.get("mode") != "qualification_solo"
'''
    if text.count(old) != 1:
        raise RuntimeError("R0 command attempt marker differs")
    text = text.replace(old, new, 1)
    old = '''    collector = (
        bundle
        / "qualification/mysql_distributed_live_readback_qualification_v1.py"
    )
    _run(
'''
    if text.count(old) != 1:
        raise RuntimeError("R0 obsolete collector marker differs")
    text = text.replace(old, "    _run(\n", 1)
    old = '''    explains = explain_documents(
        bundle, args.client_container, args.mysql_defaults_file
    )
    result = build_fresh_r0(
'''
    new = '''    explains = explain_documents(
        bundle, args.client_container, args.mysql_defaults_file
    )
    validate_gate_window(gate)
    result = build_fresh_r0(
'''
    if text.count(old) != 1:
        raise RuntimeError("R0 post gate marker differs")
    text = text.replace(old, new, 1)
    text = text.replace(
        '        sha256(args.dynamic_gate),\n        raw,\n',
        '        sha256(args.dynamic_gate),\n        support_manifest_sha256,\n        raw,\n',
        1,
    )
    if '"exec",\n                "exec",' in text:
        raise RuntimeError("duplicate Docker exec survived")
    return text.encode("utf-8")


COMMON = f'''TARGET_BUNDLE_ID = "{TARGET_ID}"
TARGET_MANIFEST_SHA256 = "{TARGET_MANIFEST_SHA}"
SUPPORT_BUNDLE_ID = "{SUPPORT_ID}"
SUPPORT_MANIFEST_SHA256 = "{SUPPORT_MANIFEST_SHA}"
SUPPORT_ENTRY_SHA256 = "{SUPPORT_ENTRY_SHA}"
SUPPORT_CORE_SHA256 = "{SUPPORT_CORE_SHA}"
TARGET_ATTEMPT = "{ATTEMPT}"
TARGET_DATABASE_CONTAINER = "{DB}"
TARGET_CLIENT_CONTAINER = "{CLIENT}"
'''


SEALER = r'''#!/usr/bin/env python3
"""Seal 15 predicted candidates and fixed Top-5 real replay evidence."""
from __future__ import annotations
import argparse, hashlib, itertools, json, math, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

''' + COMMON + r'''
PARAMETERS = ("io_block_read_cost", "memory_block_read_cost",
              "row_evaluate_cost", "disk_temptable_row_cost")
ENVELOPE = {
 "database": {"cpu_quota_cores": 5, "cpuset_cpus": "18-20,54-56",
  "cpuset_mems": "1", "memory_bytes": 6174015488,
  "memory_swap_bytes": 6174015488, "pids_max": 512,
  "io_read_bps": 272629760, "io_write_bps": 272629760,
  "innodb_buffer_pool_bytes": 4294967296, "storage_major_minor": "259:1",
  "mysql_port": 65103},
 "client": {"cpu_quota_cores": 16, "cpuset_cpus": "0-7,36-43",
  "cpuset_mems": "0", "memory_bytes": 4294967296,
  "memory_swap_bytes": 4294967296, "pids_max": 512,
  "target_mysql_device_write_prohibited": True}}

class EvidenceError(RuntimeError): pass
def canonical(v): return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha256(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""): d.update(b)
 return d.hexdigest()
def load(p,label):
 p=Path(p); s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii") != f"{sha256(p)}  {p.name}\n": raise EvidenceError(label+" seal differs")
 v=json.loads(p.read_text());
 if not isinstance(v,dict): raise EvidenceError(label+" must be object")
 return v
def utc(v):
 try: x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
 except ValueError as e: raise EvidenceError("dynamic gate timestamp differs") from e
 if x.tzinfo is None: raise EvidenceError("dynamic gate timestamp lacks timezone")
 return x.astimezone(timezone.utc)
def live_gate(g, now=None):
 c,e=utc(g.get("captured_at_utc")),utc(g.get("expires_at_utc")); n=now or datetime.now(timezone.utc)
 if not 0 < (e-c).total_seconds() <= 900 or not c <= n < e: raise EvidenceError("dynamic gate expired or lifetime differs")
def atomic(path,v):
 path=Path(path); side=Path(str(path)+".sha256")
 if path.exists() or side.exists() or path.is_symlink() or side.is_symlink(): raise EvidenceError("refusing overwrite")
 payload=canonical(v); path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name("."+path.name+f".{os.getpid()}.tmp"); st=side.with_name("."+side.name+f".{os.getpid()}.tmp")
 try:
  tmp.write_bytes(payload); st.write_text(hashlib.sha256(payload).hexdigest()+"  "+path.name+"\n",encoding="ascii")
  os.replace(tmp,path); os.replace(st,side); os.chmod(path,0o600); os.chmod(side,0o600)
 finally:
  for x in (tmp,st):
   if x.exists(): x.unlink()
def expected_subsets():
 return {tuple(PARAMETERS[i] for i in range(4) if mask&(1<<i)) for mask in range(1,16)}
def fingerprint(v,label):
 if not isinstance(v,Mapping) or v.get("schema_version") != "mysql_tpch_r0_l1_l2_l3_fingerprint_v1" or not all(isinstance(v.get(k),Mapping) for k in ("l1","l2","l3")): raise EvidenceError(label+" fingerprint differs")
 return dict(v)
def validate_prediction_rows(rows):
 if not isinstance(rows,list) or len(rows)!=15: raise EvidenceError("prediction cardinality differs")
 seen=set(); scores=[]
 for rank,row in enumerate(rows,1):
  subset=tuple(row.get("active_subset") or []); seen.add(subset)
  score=row.get("candidate_score")
  if row.get("rank")!=rank or not isinstance(score,(int,float)) or isinstance(score,bool) or not math.isfinite(float(score)): raise EvidenceError("prediction rank/score differs")
  gradient=row.get("gradient") or {}
  if set(gradient) != {"feature_ratio","predicted_resource","utilization","dScore/dU","T","confidence","g_j","eta","delta_param","candidate_score"}: raise EvidenceError("gradient closure differs")
  if any(not isinstance(gradient.get(k),Mapping) or not gradient.get(k) for k in ("feature_ratio","predicted_resource","utilization","dScore/dU","T","confidence","g_j","delta_param")): raise EvidenceError("gradient mapping evidence differs")
  if set(gradient["g_j"]) != set(subset) or set(gradient["delta_param"]) != set(subset): raise EvidenceError("gradient active-subset binding differs")
  if row.get("same_live_identity") is None or row.get("resource_envelope")!=ENVELOPE or row.get("new_container_created") is not False or row.get("accepted_rounds_credit")!=0 or row.get("accepted_round_authorized") is not False: raise EvidenceError("prediction same-live zero-credit binding differs")
  fingerprint(row.get("three_level_fingerprint"),f"prediction {rank}")
  scores.append(float(score))
 if seen != expected_subsets() or any(a < b for a,b in zip(scores,scores[1:])): raise EvidenceError("15-subset global ordering differs")
 return rows
def historical_gate(g,start,end):
 c,e=utc(g.get("captured_at_utc")),utc(g.get("expires_at_utc")); s,x=utc(start),utc(end)
 if (not 0<(e-c).total_seconds()<=900 or not c<=s<x<=e
  or g.get("attempt_token")!=TARGET_ATTEMPT
  or g.get("collector_sha256")!=SUPPORT_MANIFEST_SHA256
  or g.get("status")!="PASS"): raise EvidenceError("replay rolling gate differs")
def validate_replays(rows,predictions,identity):
 if not isinstance(rows,list) or len(rows)!=5: raise EvidenceError("Top5 replay cardinality differs")
 for rank,row in enumerate(rows,1):
  pred=predictions[rank-1]
  if (row.get("rank")!=rank or row.get("active_subset")!=pred.get("active_subset")
      or row.get("candidate_score")!=pred.get("candidate_score")
      or row.get("same_live_identity")!=identity
      or row.get("resource_envelope")!=ENVELOPE
      or row.get("resource_readback")!=ENVELOPE
      or row.get("database_counter_missing_samples")!=0
      or row.get("database_counter_timeout_count")!=0
      or not isinstance(row.get("database_counter_sample_count"),int)
      or row.get("database_counter_sample_count")<181
      or row.get("client_target_device_write_bytes")!=0
      or row.get("cpu_io_readback_drift") is not False
      or row.get("rolling_dynamic_gate_passed") is not True
      or row.get("new_container_created") is not False
      or row.get("accepted_rounds_credit")!=0
      or row.get("accepted_round_authorized") is not False): raise EvidenceError("Top5 same-live replay differs")
  historical_gate(row.get("rolling_gate") or {},row.get("measurement_started_at_utc"),row.get("measurement_ended_at_utc"))
  fingerprint(row.get("three_level_fingerprint"),f"replay {rank}")
 return rows
def build(bootstrap,gate,fresh,pred,replay,now=None):
 live_gate(gate,now); identity=bootstrap.get("same_live_identity")
 if (bootstrap.get("attempt_token")!=TARGET_ATTEMPT or gate.get("attempt_token")!=TARGET_ATTEMPT
  or fresh.get("attempt_token")!=TARGET_ATTEMPT or fresh.get("same_live_identity")!=identity
  or gate.get("collector_sha256")!=SUPPORT_MANIFEST_SHA256
  or fresh.get("support_collector_bundle_id")!=SUPPORT_BUNDLE_ID
  or fresh.get("support_collector_manifest_sha256")!=SUPPORT_MANIFEST_SHA256
  or fresh.get("formal_launch_authorized") is not False or fresh.get("accepted_rounds_credit")!=0): raise EvidenceError("same-attempt post-R0 binding differs")
 if (pred.get("attempt_token")!=TARGET_ATTEMPT or pred.get("same_live_identity")!=identity or pred.get("accepted_rounds_credit")!=0 or pred.get("accepted_round_authorized") is not False or replay.get("attempt_token")!=TARGET_ATTEMPT or replay.get("same_live_identity")!=identity or replay.get("accepted_rounds_credit")!=0 or replay.get("accepted_round_authorized") is not False): raise EvidenceError("candidate/replay journal authority differs")
 predictions=validate_prediction_rows(pred.get("candidates"));
 if any(row.get("same_live_identity")!=identity for row in predictions): raise EvidenceError("predicted same-live identity differs")
 replays=validate_replays(replay.get("replays"),predictions,identity)
 return {"schema_version":"mysql_tpch_distributed_v2_debug_round_evidence_v1","status":"PASS_DEBUG_15_SUBSETS_FIXED_TOP5_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"bundle_id":TARGET_BUNDLE_ID,"bundle_manifest_sha256":TARGET_MANIFEST_SHA256,"support_collector_bundle_id":SUPPORT_BUNDLE_ID,"support_collector_manifest_sha256":SUPPORT_MANIFEST_SHA256,"support_collector_entry_sha256":SUPPORT_ENTRY_SHA256,"attempt_token":TARGET_ATTEMPT,"same_live_identity":identity,"resource_envelope":ENVELOPE,"candidate_parameters":list(PARAMETERS),"all_nonempty_subsets_count":15,"candidate_order":"global_descending_final_candidate_score","predicted_candidates":predictions,"fixed_real_replay_top_k":5,"real_replay_top5":replays,"predicted_fingerprint_count":15,"real_fingerprint_count":5,"new_container_created":False,"qualification_credit":0,"accepted_rounds_credit":0,"accepted_round_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False}
def main():
 p=argparse.ArgumentParser();
 for n in ("bootstrap","dynamic-gate","fresh-r0","predictions","replays","output"): p.add_argument("--"+n,type=Path,required=True)
 a=p.parse_args(); b=load(a.bootstrap,"bootstrap"); g=load(a.dynamic_gate,"gate"); f=load(a.fresh_r0,"fresh R0"); pr=load(a.predictions,"predictions"); rp=load(a.replays,"replays"); result=build(b,g,f,pr,rp); live_gate(g); atomic(a.output,result); print(json.dumps(result,sort_keys=True,indent=2)); return 0
if __name__=="__main__":
 try: raise SystemExit(main())
 except (OSError,ValueError,EvidenceError) as e: raise SystemExit("NOT_READY: "+str(e))
'''


READINESS = r'''#!/usr/bin/env python3
"""Issue zero-credit post-R0/debug readiness, never formal authority."""
from __future__ import annotations
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
''' + COMMON + r'''
class ReadyError(RuntimeError): pass
def sha256(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""): d.update(b)
 return d.hexdigest()
def load(p,label):
 p=Path(p); s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii")!=f"{sha256(p)}  {p.name}\n": raise ReadyError(label+" seal differs")
 v=json.loads(p.read_text());
 if not isinstance(v,dict): raise ReadyError(label+" differs")
 return v
def utc(v):
 try:x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
 except ValueError as e: raise ReadyError("gate timestamp differs") from e
 if x.tzinfo is None: raise ReadyError("gate timezone differs")
 return x.astimezone(timezone.utc)
def verify(target,support,bootstrap,gate,fresh,debug=None,now=None):
 if Path(target).name!=TARGET_BUNDLE_ID or sha256(Path(target)/"bundle_manifest.json")!=TARGET_MANIFEST_SHA256: raise ReadyError("target bundle differs")
 if Path(support).name!=SUPPORT_BUNDLE_ID or sha256(Path(support)/"bundle_manifest.json")!=SUPPORT_MANIFEST_SHA256 or sha256(Path(support)/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py")!=SUPPORT_ENTRY_SHA256: raise ReadyError("support collector differs")
 n=now or datetime.now(timezone.utc); c,e=utc(gate.get("captured_at_utc")),utc(gate.get("expires_at_utc"))
 if not 0<(e-c).total_seconds()<=900 or not c<=n<e: raise ReadyError("dynamic gate expired")
 ident=bootstrap.get("same_live_identity")
 docs=[bootstrap,gate,fresh]+([debug] if debug else [])
 if any(d.get("attempt_token")!=TARGET_ATTEMPT for d in docs): raise ReadyError("attempt binding differs")
 if gate.get("collector_sha256")!=SUPPORT_MANIFEST_SHA256: raise ReadyError("dynamic collector binding differs")
 if fresh.get("same_live_identity")!=ident or fresh.get("support_collector_bundle_id")!=SUPPORT_BUNDLE_ID or fresh.get("accepted_rounds_credit")!=0 or fresh.get("formal_launch_authorized") is not False or fresh.get("formal_r0_authorized") is not False: raise ReadyError("fresh R0 differs")
 if debug and (debug.get("schema_version")!="mysql_tpch_distributed_v2_debug_round_evidence_v1" or debug.get("same_live_identity")!=ident or debug.get("all_nonempty_subsets_count")!=15 or debug.get("fixed_real_replay_top_k")!=5 or debug.get("accepted_rounds_credit")!=0 or debug.get("formal_launch_authorized") is not False): raise ReadyError("debug evidence differs")
 return {"schema_version":"mysql_tpch_postqualification_zero_credit_readiness_v1","status":"READY_DEBUG_ONLY" if debug is None else "PASS_DEBUG_ZERO_CREDIT_NOT_FORMAL_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"bundle_id":TARGET_BUNDLE_ID,"bundle_manifest_sha256":TARGET_MANIFEST_SHA256,"support_collector_bundle_id":SUPPORT_BUNDLE_ID,"support_collector_manifest_sha256":SUPPORT_MANIFEST_SHA256,"attempt_token":TARGET_ATTEMPT,"same_live_identity":ident,"dynamic_gate_expires_at_utc":gate.get("expires_at_utc"),"qualification_credit":0,"accepted_rounds_credit":0,"accepted_round_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False}
def main():
 p=argparse.ArgumentParser(); p.add_argument("--bundle",type=Path,required=True); p.add_argument("--support-bundle",type=Path,required=True)
 for n in ("bootstrap","dynamic-gate","fresh-r0","debug-round","output"): p.add_argument("--"+n,type=Path,required=n!="debug-round")
 a=p.parse_args(); vals=[load(getattr(a,n.replace("-","_")),n) for n in ("bootstrap","dynamic-gate","fresh-r0")]; debug=load(a.debug_round,"debug") if a.debug_round else None; result=verify(a.bundle.resolve(),a.support_bundle.resolve(),*vals,debug); out=a.output
 if out.exists() or Path(str(out)+".sha256").exists(): raise ReadyError("refusing overwrite")
 payload=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode(); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(payload); Path(str(out)+".sha256").write_text(hashlib.sha256(payload).hexdigest()+"  "+out.name+"\n",encoding="ascii"); print(json.dumps(result,sort_keys=True,indent=2)); return 0
if __name__=="__main__":
 try: raise SystemExit(main())
 except (OSError,ValueError,ReadyError) as e: raise SystemExit("NOT_READY: "+str(e))
'''


TEST = r'''from datetime import datetime, timezone
import importlib.util, itertools, json
from pathlib import Path
import pytest
BUNDLE=Path(__file__).resolve().parents[1]
def module(name,path):
 s=importlib.util.spec_from_file_location(name,BUNDLE/path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
S=module("sealer","debug/mysql_tpch_same_live_top5_sealer_v1.py")
R=module("r0","qualification/mysql_tpch_v2_fresh_r0_runner_v2.py")
def fp(): return {"schema_version":"mysql_tpch_r0_l1_l2_l3_fingerprint_v1","l1":{},"l2":{},"l3":{}}
def rows(identity=None):
 identity=identity or {"database_container_id":"d","client_container_id":"c"}
 subsets=[tuple(S.PARAMETERS[i] for i in range(4) if mask&(1<<i)) for mask in range(1,16)]
 return [{"rank":i,"active_subset":list(x),"candidate_score":16-i,"gradient":{k:{name:1.0 for name in x} for k in ("feature_ratio","predicted_resource","utilization","dScore/dU","T","confidence","g_j","delta_param")} | {"eta":1.0,"candidate_score":16-i},"three_level_fingerprint":fp(),"same_live_identity":identity,"resource_envelope":S.ENVELOPE,"new_container_created":False,"accepted_rounds_credit":0,"accepted_round_authorized":False} for i,x in enumerate(subsets,1)]
def gate(): return {"status":"PASS","attempt_token":S.TARGET_ATTEMPT,"collector_sha256":S.SUPPORT_MANIFEST_SHA256,"captured_at_utc":"2026-08-03T00:00:00Z","expires_at_utc":"2026-08-03T00:15:00Z"}
def replay_rows(p,identity):
 return [{"rank":i,"active_subset":p[i-1]["active_subset"],"candidate_score":p[i-1]["candidate_score"],"same_live_identity":identity,"resource_envelope":S.ENVELOPE,"resource_readback":S.ENVELOPE,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"database_counter_sample_count":181,"client_target_device_write_bytes":0,"cpu_io_readback_drift":False,"rolling_dynamic_gate_passed":True,"rolling_gate":gate(),"measurement_started_at_utc":"2026-08-03T00:00:10Z","measurement_ended_at_utc":"2026-08-03T00:14:50Z","new_container_created":False,"accepted_rounds_credit":0,"accepted_round_authorized":False,"three_level_fingerprint":fp()} for i in range(1,6)]
def test_exact_15_subsets_and_fixed_top5():
 identity={"database_container_id":"d","client_container_id":"c"}; p=rows(identity); S.validate_prediction_rows(p)
 r=replay_rows(p,identity)
 S.validate_replays(r,p,identity)
def test_rejects_wrong_top5_and_counter_timeout():
 identity={"database_container_id":"d","client_container_id":"c"}; p=rows(identity); r=replay_rows(p,identity); r[0]["database_counter_timeout_count"]=1
 with pytest.raises(S.EvidenceError): S.validate_replays(r,p,identity)
def test_dynamic_gate_is_unexpired_and_bounded():
 gate={"captured_at_utc":"2026-08-03T00:00:00Z","expires_at_utc":"2026-08-03T00:15:00Z"}; S.live_gate(gate,datetime(2026,8,3,0,14,tzinfo=timezone.utc))
 with pytest.raises(S.EvidenceError): S.live_gate(gate,datetime(2026,8,3,0,15,tzinfo=timezone.utc))
def test_r0_uses_support_collector_and_has_no_duplicate_exec():
 source=(BUNDLE/"qualification/mysql_tpch_v2_fresh_r0_runner_v2.py").read_text()
 assert "verify_support_bundle(args.support_bundle)" in source
 assert "mysql_tpch_tpcc_shared_collector_entry_current_v1.py" in source
 assert '"exec",\n                "exec",' not in source
 assert source.count("validate_gate_window(gate)") >= 2
def test_zero_credit_flags_are_explicit():
 source=(BUNDLE/"debug/mysql_tpch_same_live_top5_sealer_v1.py").read_text()
 for token in ('"accepted_rounds_credit":0','"accepted_round_authorized":False','"formal_launch_authorized":False'): assert token in source
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    if sha256(TARGET / "bundle_manifest.json") != TARGET_MANIFEST_SHA:
        raise RuntimeError("target manifest differs")
    if sha256(SUPPORT / "bundle_manifest.json") != SUPPORT_MANIFEST_SHA:
        raise RuntimeError("support manifest differs")
    content = {
        "qualification/mysql_tpch_v2_fresh_r0_runner_v2.py": patch_r0(),
        "qualification/mysql_tpch_postqualification_readiness_v1.py": READINESS.encode(),
        "debug/mysql_tpch_same_live_top5_sealer_v1.py": SEALER.encode(),
        "tests/test_mysql_tpch_postqualification_r0_top5_successor_v1.py": TEST.encode(),
        "builder/build_mysql_tpch_postqualification_r0_top5_successor_v1.py": HERE.read_bytes(),
        "config/mysql_tpch_ff313_node04_same_live_debug_v1.json": canonical({
            "schema_version": "mysql_tpch_ff313_node04_same_live_debug_config_v1",
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True, "host_id": "node04",
            "attempt_token": ATTEMPT, "target_bundle_id": TARGET_ID,
            "support_collector_bundle_id": SUPPORT_ID,
            "database_container": DB, "client_container": CLIENT,
            "storage_device": "/dev/nvme0n1", "storage_major_minor": "259:1",
            "database_envelope": {"cpu_quota_cores": 5, "cpuset_cpus": "18-20,54-56", "cpuset_mems": "1", "memory_bytes": 6174015488, "memory_swap_bytes": 6174015488, "pids_max": 512, "io_read_bps": 272629760, "io_write_bps": 272629760, "innodb_buffer_pool_bytes": 4294967296, "mysql_port": 65103},
            "client_envelope": {"cpu_quota_cores": 16, "cpuset_cpus": "0-7,36-43", "cpuset_mems": "0", "memory_bytes": 4294967296, "memory_swap_bytes": 4294967296, "pids_max": 512, "target_mysql_device_write_prohibited": True},
            "active_parameters": ["io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost", "disk_temptable_row_cost"],
            "all_nonempty_subsets": 15, "fixed_real_replay_top_k": 5,
            "new_container_creation_prohibited": True, "same_live_identity_required": True,
            "ongoing_unexpired_dynamic_gate_required": True,
            "accepted_rounds_credit": 0, "accepted_round_authorized": False,
            "formal_launch_authorized": False,
        }),
        "templates/main_agent_commands.json": canonical({
            "schema_version": "mysql_tpch_postqualification_main_agent_commands_v1",
            "execution_order": ["fresh_r0", "debug_readiness", "top5_seal", "debug_evidence_readiness"],
            "fresh_r0": ["[SEALED_PYTHON]", "[THIS_BUNDLE]/qualification/mysql_tpch_v2_fresh_r0_runner_v2.py", "--bundle", "[EXACT_FF313_BUNDLE]", "--support-bundle", "[EXACT_86232_SUPPORT_BUNDLE]", "--bootstrap", "[SAME_LIVE_BOOTSTRAP_JSON]", "--dynamic-gate", "[UNEXPIRED_DYNAMIC_GATE_JSON]", "--command-spec", "[FRESH_QUALIFICATION_SOLO_COMMAND_JSON]", "--main-agent-authority", "[FRESH_SHORT_LIVED_AUTHORITY_JSON]", "--main-agent-token-file", "[MODE_0600_TOKEN]", "--database-container", DB, "--client-container", CLIENT, "--mysql-defaults-file", "[PRIVATE_MYSQL_CNF]", "--output", "[NONEXISTENT_FRESH_R0_JSON]"],
            "debug_readiness": ["[SEALED_PYTHON]", "[THIS_BUNDLE]/qualification/mysql_tpch_postqualification_readiness_v1.py", "--bundle", "[EXACT_FF313_BUNDLE]", "--support-bundle", "[EXACT_86232_SUPPORT_BUNDLE]", "--bootstrap", "[SAME_LIVE_BOOTSTRAP_JSON]", "--dynamic-gate", "[UNEXPIRED_DYNAMIC_GATE_JSON]", "--fresh-r0", "[SEALED_FRESH_R0_JSON]", "--output", "[NONEXISTENT_DEBUG_READINESS_JSON]"],
            "top5_seal": ["[SEALED_PYTHON]", "[THIS_BUNDLE]/debug/mysql_tpch_same_live_top5_sealer_v1.py", "--bootstrap", "[SAME_LIVE_BOOTSTRAP_JSON]", "--dynamic-gate", "[UNEXPIRED_DYNAMIC_GATE_JSON]", "--fresh-r0", "[SEALED_FRESH_R0_JSON]", "--predictions", "[SEALED_15_CANDIDATE_JOURNAL_JSON]", "--replays", "[SEALED_FIXED_TOP5_REPLAY_JOURNAL_JSON]", "--output", "[NONEXISTENT_DEBUG_EVIDENCE_JSON]"],
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True, "remote_actions_by_builder": 0,
            "formal_launch_authorized": False, "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
        }),
    }
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    unsigned = {
        "schema_version": "mysql_tpch_postqualification_r0_top5_successor_bundle_v1",
        "status": "PASS_IMMUTABLE_LOCAL_SUCCESSOR_NOT_REMOTE_EXECUTION_AUTHORITY",
        "immutable": True, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID, "target_manifest_sha256": TARGET_MANIFEST_SHA,
        "support_collector_bundle_id": SUPPORT_ID,
        "support_collector_manifest_sha256": SUPPORT_MANIFEST_SHA,
        "support_collector_entry_sha256": SUPPORT_ENTRY_SHA,
        "support_collector_core_sha256": SUPPORT_CORE_SHA,
        "attempt_token": ATTEMPT, "host_id": "node04",
        "database_container": DB, "client_container": CLIENT,
        "r0_fixes": ["explicit_support_collector", "duplicate_exec_absent", "dynamic_gate_expiry_before_and_after", "exact_attempt_binding", "explicit_zero_credit_authority"],
        "top5_fixes": ["node04_259_1_same_live_envelope", "all_15_nonempty_subsets", "global_descending_rank", "fixed_top5_real_replay", "15_plus_5_l1_l2_l3_fingerprints", "counter_timeout_fail_closed", "zero_credit_authority"],
        "new_container_creation_prohibited": True,
        "formal_launch_authorized": False, "formal_r0_authorized": False,
        "accepted_round_authorized": False, "accepted_rounds_credit": 0,
        "remote_actions": 0, "files": files,
    }
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    final = OUT / bundle_id
    if final.exists():
        if sha256(final / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise RuntimeError("existing bundle differs")
        return final
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpch-postqual-", dir=OUT))
    try:
        for name, payload in content.items():
            path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
        payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(sha_bytes(payload)+"  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.replace(stage, final)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return final


if __name__ == "__main__":
    bundle = build()
    print(json.dumps({"bundle": str(bundle), "bundle_id": bundle.name,
                      "manifest_sha256": sha256(bundle / "bundle_manifest.json"),
                      "formal_launch_authorized": False,
                      "accepted_rounds_credit": 0, "remote_actions": 0},
                     sort_keys=True))
