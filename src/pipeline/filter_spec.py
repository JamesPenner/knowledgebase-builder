from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FilterSpec:
    file_type: str = "all"
    glob: str | None = None
    count_limit: int | None = None
    modified_after: str | None = None
    exclude_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "FilterSpec":
        return cls(
            file_type=d.get("file_type", "all"),
            glob=d.get("glob"),
            count_limit=d.get("count_limit"),
            modified_after=d.get("modified_after"),
            exclude_patterns=d.get("exclude_patterns") or [],
        )

    def to_dict(self) -> dict:
        out: dict = {}
        if self.file_type != "all":
            out["file_type"] = self.file_type
        if self.glob is not None:
            out["glob"] = self.glob
        if self.count_limit is not None:
            out["count_limit"] = self.count_limit
        if self.modified_after is not None:
            out["modified_after"] = self.modified_after
        if self.exclude_patterns:
            out["exclude_patterns"] = self.exclude_patterns
        return out
