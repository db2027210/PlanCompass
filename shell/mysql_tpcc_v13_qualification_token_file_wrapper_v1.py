#!/usr/bin/env python3
"""Run the sealed qualification CLI without exposing its authority token in argv.

The token is read once from a regular owner-only file, then supplied directly
to the imported program's argument parser in memory.  It is not written to
stdout/stderr/result JSON and is not present in /proc/<pid>/cmdline.
"""
from __future__ import annotations
import importlib.util, os, stat, sys
from pathlib import Path
TARGET=Path(__file__).resolve().with_name("mysql_distributed_live_readback_qualification_v1.py")
class Error(RuntimeError):pass
def token_file(path):
 p=Path(path).resolve(strict=True);s=p.stat()
 if p.is_symlink() or not p.is_file() or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600:raise Error("token file must be a current-user regular 0600 file")
 value=p.read_text(encoding="utf-8").strip()
 if not value or "\x00" in value or "\n" in value:raise Error("token file content differs")
 return value
def main():
 try:i=sys.argv.index("--main-agent-token-file")
 except ValueError:raise Error("--main-agent-token-file is required")
 if i+1>=len(sys.argv):raise Error("token file path is absent")
 token=token_file(sys.argv[i+1]);argv=sys.argv[:i]+sys.argv[i+2:]+["--main-agent-token",token]
 spec=importlib.util.spec_from_file_location("sealed_qualification",TARGET)
 if spec is None or spec.loader is None:raise Error("sealed qualification program is absent")
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);sys.argv=argv;return module.main()
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
