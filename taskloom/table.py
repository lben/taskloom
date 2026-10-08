"""Tabular data passed between blocks, stored as a Parquet file on disk."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class Table:
    path: Path

    @property
    def num_rows(self) -> int:
        return pq.ParquetFile(self.path).metadata.num_rows

    def __len__(self) -> int:
        return self.num_rows

    @property
    def schema(self) -> dict:
        return dict(pl.read_parquet_schema(self.path))

    def scan(self) -> pl.LazyFrame:
        """Lazy Polars view; works on data larger than memory."""
        return pl.scan_parquet(self.path)

    def head(self, n: int = 10) -> pl.DataFrame:
        return self.scan().head(n).collect()

    def summary(self) -> str:
        return f"{self.num_rows:,} rows × {len(self.schema)} columns"

    @staticmethod
    def is_frame(value) -> bool:
        if isinstance(value, (pl.DataFrame, pl.LazyFrame, pa.Table)):
            return True
        pandas = sys.modules.get("pandas")  # only if the user's code already imported it
        return pandas is not None and isinstance(value, pandas.DataFrame)

    @classmethod
    def from_frame(cls, value, path: Path) -> "Table":
        """Store a Polars/pandas/Arrow frame as a Table. LazyFrames stream to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, pl.LazyFrame):
            value.sink_parquet(path)
        elif isinstance(value, pl.DataFrame):
            value.write_parquet(path)
        elif isinstance(value, pa.Table):
            pq.write_table(value, path)
        else:
            pq.write_table(pa.Table.from_pandas(value, preserve_index=False), path)
        return cls(path)
