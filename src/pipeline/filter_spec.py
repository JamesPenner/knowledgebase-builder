from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FilterSpec:
    """Disk-scanning filter applied at ingest time (filesystem walk)."""
    glob: str | None = None
    count_limit: int | None = None
    modified_after: str | None = None
    exclude_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "FilterSpec":
        return cls(
            glob=d.get("glob"),
            count_limit=d.get("count_limit"),
            modified_after=d.get("modified_after"),
            exclude_patterns=d.get("exclude_patterns") or [],
        )

    def to_dict(self) -> dict:
        out: dict = {}
        if self.glob is not None:
            out["glob"] = self.glob
        if self.count_limit is not None:
            out["count_limit"] = self.count_limit
        if self.modified_after is not None:
            out["modified_after"] = self.modified_after
        if self.exclude_patterns:
            out["exclude_patterns"] = self.exclude_patterns
        return out


@dataclass
class CorpusFilterSpec:
    """Post-ingest filter applied as SQL WHERE clauses against the corpus DB.

    All fields are optional; None means "no filter on this dimension".
    The SQL fragment is appended to queries that alias the files table as 'f'.
    """
    source_id: int | None = None
    folder_prefix: str | None = None   # parent directory path; files must reside under it
    file_type: str | None = None       # "images" | "video" | "audio"
    date_from: str | None = None       # ISO date "YYYY-MM-DD"; matched against f.mtime
    date_to: str | None = None         # ISO date "YYYY-MM-DD" (inclusive)
    name_pattern: str | None = None    # fnmatch-style pattern matched against f.filename

    def to_sql_fragment(self) -> tuple[str, list]:
        """Return (" AND clause ...", [params]) to append to a WHERE 1=1 query."""
        clauses: list[str] = []
        params: list = []

        if self.source_id is not None:
            clauses.append("f.source_id = ?")
            params.append(self.source_id)

        if self.folder_prefix is not None:
            # Match files whose path starts with folder_prefix + path separator.
            # Without ESCAPE, backslash is literal in SQLite LIKE, % is wildcard.
            prefix = self.folder_prefix.rstrip("/\\")
            clauses.append("(f.path LIKE ? OR f.path LIKE ?)")
            params.extend([prefix + "/%", prefix + "\\%"])

        if self.file_type is not None:
            clauses.append("f.file_type = ?")
            params.append(self.file_type)

        if self.date_from is not None:
            clauses.append("date(f.mtime, 'unixepoch') >= ?")
            params.append(self.date_from)

        if self.date_to is not None:
            clauses.append("date(f.mtime, 'unixepoch') <= ?")
            params.append(self.date_to)

        if self.name_pattern is not None:
            # Convert fnmatch wildcards to SQL LIKE wildcards.
            # Escape existing SQL specials first, then convert * and ?.
            sql_pat = (
                self.name_pattern
                .replace("%", r"\%")
                .replace("_", r"\_")
                .replace("*", "%")
                .replace("?", "_")
            )
            clauses.append(r"f.filename LIKE ? ESCAPE '\'")
            params.append(sql_pat)

        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    @classmethod
    def from_dict(cls, d: dict) -> "CorpusFilterSpec":
        return cls(
            source_id=_int_or_none(d.get("source_id")),
            folder_prefix=d.get("folder_prefix") or None,
            file_type=d.get("file_type") or None,
            date_from=d.get("date_from") or None,
            date_to=d.get("date_to") or None,
            name_pattern=d.get("name_pattern") or None,
        )

    def to_dict(self) -> dict:
        out: dict = {}
        if self.source_id is not None:
            out["source_id"] = self.source_id
        if self.folder_prefix is not None:
            out["folder_prefix"] = self.folder_prefix
        if self.file_type is not None:
            out["file_type"] = self.file_type
        if self.date_from is not None:
            out["date_from"] = self.date_from
        if self.date_to is not None:
            out["date_to"] = self.date_to
        if self.name_pattern is not None:
            out["name_pattern"] = self.name_pattern
        return out

    def summary(self) -> str:
        """Human-readable one-liner for display (e.g. 'Images · Italy · 2023-07–2023-08')."""
        parts: list[str] = []
        if self.file_type:
            parts.append(self.file_type.title())
        if self.folder_prefix:
            parts.append(Path(self.folder_prefix).name or self.folder_prefix)
        if self.date_from and self.date_to:
            parts.append(f"{self.date_from}–{self.date_to}")
        elif self.date_from:
            parts.append(f"from {self.date_from}")
        elif self.date_to:
            parts.append(f"to {self.date_to}")
        if self.name_pattern:
            parts.append(self.name_pattern)
        return " · ".join(parts) if parts else "All files"

    def is_empty(self) -> bool:
        return all(v is None for v in (
            self.source_id, self.folder_prefix, self.file_type,
            self.date_from, self.date_to, self.name_pattern,
        ))


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
