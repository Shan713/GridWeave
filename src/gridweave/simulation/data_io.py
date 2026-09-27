"""CSV persistence for demand series (long format: timestamp, building_id, demand_kw)."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from gridweave.models.demand import DemandSample

FIELDS = ("timestamp", "building_id", "demand_kw")


def write_demand_csv(path: str | Path, series: Mapping[str, Sequence[DemandSample]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for building_id, samples in series.items():
            for s in samples:
                writer.writerow((s.timestamp.isoformat(), building_id, f"{s.demand_kw:.4f}"))
    return path


def read_demand_csv(path: str | Path) -> dict[str, list[DemandSample]]:
    out: dict[str, list[DemandSample]] = defaultdict(list)
    with Path(path).open(newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["building_id"]].append(
                DemandSample(datetime.fromisoformat(row["timestamp"]), float(row["demand_kw"]))
            )
    return {k: sorted(v) for k, v in out.items()}
