#!/usr/bin/env python3
"""Build the immutable local TPC-C typed-registry-v2 sealer successor."""
from __future__ import annotations
import hashlib, json, os, secrets, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SEALER = ROOT / "shell/mysql_tpcc_typed_trace_registry_sealer_v2.py"
TEST = ROOT / "shell/test_mysql_tpcc_typed_trace_registry_sealer_v2.py"
MATERIALS = ROOT / "shell/mysql_tpcc_typed_registry_seal_materials_v1.py"
V13 = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v13/4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
V13_SHA = "2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90"
OUT = ROOT / "artifacts/mysql_tpcc_typed_registry_sealer_successor_v1"

def canonical(value): return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()
def main():
    if V13.is_symlink() or sha(V13 / "bundle_manifest.json") != V13_SHA: raise RuntimeError("v13 binding differs")
    payloads={"registry/mysql_tpcc_typed_trace_registry_sealer_v2.py":SEALER.read_bytes(), "materials/mysql_tpcc_typed_registry_seal_materials_v1.py":MATERIALS.read_bytes(), "tests/test_mysql_tpcc_typed_trace_registry_sealer_v2.py":TEST.read_bytes(), "authority/v13_bundle_manifest.json":(V13/"bundle_manifest.json").read_bytes(), "authority/build_mysql_tpcc_typed_registry_sealer_successor_v1.py":SELF.read_bytes()}
    files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payloads.items())}
    identity={"schema_version":"mysql_tpcc_typed_registry_sealer_successor_bundle_v1","status":"SEALED_LOCAL_REAL_EVIDENCE_REGISTRY_PUBLISHER_NOT_FORMAL_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"source_v13_bundle_id":V13.name,"source_v13_manifest_sha256":V13_SHA,"registry_schema":"mysql_tpcc_typed_trace_registry_bundle_v2","real_trace_required":True,"synthetic_trace_prohibited":True,"qps_resource_input_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files}
    bundle_id=hashlib.sha256(canonical(identity)).hexdigest(); manifest={**identity,"bundle_id":bundle_id}; target=OUT/bundle_id
    if target.exists() or target.is_symlink():
        if json.loads((target/"bundle_manifest.json").read_text()) != manifest: raise RuntimeError("existing bundle differs")
    else:
        OUT.mkdir(parents=True,exist_ok=True); stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(8)); stage.mkdir(mode=0o700)
        try:
            for name,data in payloads.items():
                p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);os.chmod(p,0o444)
            raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n",encoding="ascii")
            os.rename(stage,target)
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise
    print(json.dumps({"status":"PASS_SEALED_LOCAL_REGISTRY_SEALER_NOT_FORMAL_AUTHORITY","bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":sha(target/"bundle_manifest.json"),"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
    try: main()
    except Exception as exc: sys.stderr.write("NOT_READY: %s\n"%exc);raise SystemExit(2)
