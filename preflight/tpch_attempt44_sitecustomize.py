"""Module."""


from __future__ import annotations

from pathlib import Path
from typing import Any


def _install_pandas_parquet_fallback() -> None:
    try:
        import pandas as pd
    except ImportError:
        return

    original_read_parquet = pd.read_parquet

    def read_parquet_with_csv_fallback(path: Any, *args: Any, **kwargs: Any) -> Any:
        csv_path = Path(path).with_suffix(".csv")
        if csv_path.is_file():
            columns = kwargs.get("columns")
            return pd.read_csv(csv_path, usecols=columns)
        return original_read_parquet(path, *args, **kwargs)

    pd.read_parquet = read_parquet_with_csv_fallback


_install_pandas_parquet_fallback()
