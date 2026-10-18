#!/usr/bin/env python3
"""Single-worker interrupted-reset recovery with derived runtime roots."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "recover_mysql_xp8_single_interrupted_reset_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = 'runtime_root = Path(str(frozen[args.worker_id].get("runtime_root") or ""))'
new = '''worker_storage = dict(
        (freeze.get("storage_by_worker") or {}).get(args.worker_id)
        or freeze["storage"]
    )
    runtime_root = Path(str(
        frozen[args.worker_id].get("runtime_root")
        or (Path(str(worker_storage["runtime_root"])) / args.worker_id)
    ))'''
if text.count(old) != 1:
    raise RuntimeError("single-worker recovery v2 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._recover_mysql_xp8_single_interrupted_reset_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
