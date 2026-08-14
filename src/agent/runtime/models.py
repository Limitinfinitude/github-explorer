from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ToolRisk(str, Enum):
    READ = "read"
    WRITE_SAFE = "write_safe"
    PROCESS = "process"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"
    EXTERNAL = "external"


@dataclass(frozen=True)
class Workspace:
    session_id: str
    root: Path
    current_path: Path | None = None


@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    error: Optional[str] = None
    changed_files: list[str] = field(default_factory=list)
    process_id: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_reason: Optional[str] = None
    error_kind: Optional[str] = None

    @classmethod
    def ok(cls, **kwargs: Any) -> "ToolResult":
        return cls(success=True, **kwargs)

    @classmethod
    def fail(cls, error: str, **kwargs: Any) -> "ToolResult":
        return cls(success=False, error=error, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "output": self.output,
            "error": self.error,
            "changed_files": self.changed_files,
            "process_id": self.process_id,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_reason": self.confirmation_reason,
            "error_kind": self.error_kind,
        }

    def validation_error(self) -> str | None:
        checks = [
            ("success", self.success, bool),
            ("data", self.data, dict),
            ("output", self.output, str),
            ("changed_files", self.changed_files, list),
            ("requires_confirmation", self.requires_confirmation, bool),
        ]
        for name, value, expected in checks:
            if not isinstance(value, expected):
                return f"{name} 必须是 {expected.__name__}"
        if any(not isinstance(path, str) for path in self.changed_files):
            return "changed_files 必须是字符串数组"
        optional_strings = [
            ("error", self.error),
            ("process_id", self.process_id),
            ("confirmation_reason", self.confirmation_reason),
            ("error_kind", self.error_kind),
        ]
        for name, value in optional_strings:
            if value is not None and not isinstance(value, str):
                return f"{name} 必须是字符串或 null"
        if self.success and self.error is not None:
            return "成功结果不能包含 error"
        if self.success and self.error_kind is not None:
            return "成功结果不能包含 error_kind"
        if self.success and self.requires_confirmation:
            return "成功结果不能等待确认"
        if self.requires_confirmation and not self.confirmation_reason:
            return "等待确认必须包含 confirmation_reason"
        if not self.success and not self.requires_confirmation and not self.error:
            return "失败结果必须包含 error"
        return None
