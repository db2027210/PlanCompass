import hashlib, json, stat
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def test_manifest_and_authority_boundary():
 m=json.loads((ROOT/"bundle_manifest.json").read_text())
 assert m["schema_version"]=="mysql_xp_v26_runtime_producer_successor_bundle_v1"
 assert m["collector_contract_repaired"] is True
 assert m["round_producer_present"] is True
 assert m["real_replay_executor_present"] is True
 assert m["formal10_fail_closed_controller_present"] is True
 assert m["required_nonempty_subsets"]==127 and m["fixed_real_replay_top_k"]==5
 assert m["v8_producer_used"] is False and m["replacement_search_algorithm_used"] is False
 assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_rounds_credit"]==0
def test_closure_identity_immutable():
 m=json.loads((ROOT/"bundle_manifest.json").read_text()); actual={}
 for p in ROOT.rglob("*"):
  assert not p.is_symlink() and not stat.S_IMODE(p.stat().st_mode)&0o222
  rel=p.relative_to(ROOT).as_posix()
  if p.is_file() and rel not in {"bundle_manifest.json","bundle_manifest.json.sha256"}: actual[rel]=sha(p)
 assert actual==m["files"]
 unsigned=dict(m); bid=unsigned.pop("bundle_id")
 payload=json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
 assert hashlib.sha256(payload).hexdigest()==bid==ROOT.name
