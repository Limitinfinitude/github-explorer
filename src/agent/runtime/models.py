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
        }
