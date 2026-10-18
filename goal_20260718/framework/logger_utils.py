import json
import time
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any


class OptimizationLogger:
    """OptimizationLogger."""


    def __init__(self, log_path: Optional[str] = None, verbose: bool = True):
        self.verbose = verbose
        self.file_path = None
        self.file_handle = None

        if log_path:
            base_name = os.path.basename(log_path)
            has_embedded_timestamp = bool(
                re.search(r"_\d{4}-\d{2}-\d{2}_\d{6}\.[^.]+$", base_name)
            )
            if has_embedded_timestamp:
                self.file_path = log_path
            else:
                base, ext = os.path.splitext(log_path)
                date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                self.file_path = f"{base}_{date_str}{ext}"
            try:
                log_dir = os.path.dirname(self.file_path)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)
                self.file_handle = open(self.file_path, "a", encoding="utf-8")
            except Exception as e:

                if self.verbose:
                    print(f"[WARN] Failed to open log file {self.file_path}: {e}", flush=True)

    def log(self, msg: str, force: bool = False) -> None:
        """log."""


        if self.verbose or force:
            print(msg, flush=True)

        if self.file_handle:
            try:
                record = {
                    "event": "log",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "message": msg
                }
                self.file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.file_handle.flush()
            except Exception:
                pass

    def log_struct(self, data: Dict[str, Any]) -> None:
        """log struct."""


        if self.file_handle:
            try:

                if "timestamp" not in data:
                    data["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

                # Fallback to string for non-JSON-serializable objects (e.g., numpy types)
                self.file_handle.write(
                    json.dumps(data, ensure_ascii=False, default=str) + "\n"
                )
                self.file_handle.flush()
            except Exception:
                pass

    def close(self) -> None:
        """close."""
        if self.file_handle:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None

    def get_log_path(self) -> Optional[str]:
        """get log path."""
        return self.file_path
