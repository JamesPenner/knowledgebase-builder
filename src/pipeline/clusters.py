"""Shared typing for per-file cluster assignments (face regions, voice segments)."""
from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClusterAssignment:
    """A single file's (or region's/segment's) membership in a cluster.

    `extra` holds fields specific to one cluster type (e.g. face `bbox`,
    voice `start_ms`/`end_ms`) that don't generalise across types.
    """
    file_path: str
    person_id: int | None
    score: float | None                        # similarity; higher = better
    cluster_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def write_cluster_csv(
    path: Path,
    assignments: Sequence[ClusterAssignment],
    fieldnames: list[str],
    row_fn: Callable[[ClusterAssignment], dict[str, Any]],
) -> None:
    """Write cluster assignments to CSV. `row_fn` maps an assignment to its row dict."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row_fn(a) for a in assignments)
