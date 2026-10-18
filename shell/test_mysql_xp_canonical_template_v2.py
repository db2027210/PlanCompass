from __future__ import annotations
import importlib.util, json, os, unittest
from pathlib import Path
S=Path(__file__).with_name("mysql_xp_canonical_template_v2.py")
if not S.is_file():
 S=Path(__file__).resolve().parents[1]/"runtime/mysql_xp_canonical_template_v2.py"
spec=importlib.util.spec_from_file_location("canonical",S);C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)

class Tests(unittest.TestCase):
 def test_frozen_constants(self):
  self.assertEqual(C.MANIFEST_SHA256,"fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa")
  self.assertEqual(C.TREE_SHA256,"c512bc478abb3e210ddafe3e5f69cf2058f8bfdbd7e90afa251b9b9f156c10ac")
  self.assertEqual((C.DATABASE_SIZE_BYTES,C.REGULAR_FILE_COUNT,C.DIRECTORY_COUNT,C.SYMLINK_COUNT),(11033004101,173,6,1))
  self.assertEqual(len(C.TABLE_SHA256),8);self.assertEqual(sum(C.ROWS.values()),87060000)
 def test_scan_algorithm_file_directory_and_symlink(self):
  import tempfile
  with tempfile.TemporaryDirectory() as td:
   r=Path(td);(r/"d").mkdir();(r/"d/f").write_bytes(b"abc");os.symlink(C.MYSQL_SOCK_TARGET,r/"mysql.sock")
   v=C.scan_datadir(r);self.assertEqual((v["regular_file_count"],v["directory_count"],v["symlink_count"],v["database_size_bytes"]),(1,1,1,3));self.assertEqual(v["symlinks"],[{"path":"mysql.sock","target":C.MYSQL_SOCK_TARGET}])
 def test_authoritative_manifest_bytes_are_available(self):
  root=Path(__file__).resolve().parents[1];p=root/"authority/canonical_xp_database_manifest.json"
  if not p.exists():p=root/"history/unified_formal/six_session_20260721_v2/mysql/preflight/template_build_attempts/hybench_20260721T065743Z_4084522_09ac727a/database_manifest.json"
  self.assertEqual(C.sha256_file(p),C.MANIFEST_SHA256);v=json.loads(p.read_text());self.assertEqual(v["template_snapshot_sha256"],C.TREE_SHA256);self.assertEqual(v["row_counts"],C.ROWS)
if __name__=="__main__":unittest.main()
