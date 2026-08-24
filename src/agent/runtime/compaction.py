import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"\b(?:sk|gh[opusr]|github_pat|tp)-?[A-Za-z0-9_-]{12,}\b"),
)
_FAILURE_MARKERS = ("失败", "错误", "异常", "被占用", "failed", "error", "exception")

# Claude Code 9 节压缩提示词（partial-compaction-instructions v2.1.205 结构）。
# 摘要由模型生成：<analysis> 思考块 + <summary> 9 节正文；安全相关约束必须逐字保留。
LLM_SUMMARIZATION_SYSTEM = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Do NOT use any tool. You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

Your task is to create a detailed summary of this conversation. This summary will be placed at the start of a continuing session; newer messages that build on this context will follow after your summary (you do not see them here). Summarize thoroughly so that someone reading only your summary and then the newer messages can fully understand what happened and continue the work.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:
1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like file names, function signatures, file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback, especially if the user told you to do something differently.
   - Note any security-relevant instructions or constraints the user stated (e.g., sensitive files or data to avoid, operations that must not be performed, credential or secret handling rules). These MUST be preserved verbatim in the summary so they continue to apply after compaction.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary must include the following 9 sections:
1. Primary Request and Intent: Capture the user's explicit requests and intents in detail
2. Key Technical Concepts: List important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. Preserve any security-relevant instructions or constraints verbatim so they remain in effect after compaction.
7. Pending Tasks: Outline any pending tasks.
8. Work Completed: Describe what was accomplished by the end of this portion.
9. Context for Continuing Work: Summarize any context, decisions, or state that would be needed to understand and continue the work in subsequent messages.

Write in the same language as the conversation. Be precise and thorough — err on the side of including information that would prevent duplicate work or repeated mistakes."""


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(marker in key.lower() for marker in ("key", "token", "secret", "password", "authorization"))
            else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


@dataclass
class ContextHandoff:
    goal: str = ""
    progress: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    workspace_root: str = ""
    current_path: str = ""
    changed_files: list[str] = field(default_factory=list)
    verification: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    source_message_count: int = 0

    def to_context_message(self) -> dict:
        payload = json.dumps(_safe_value(asdict(self)), ensure_ascii=False, separators=(",", ":"))
        return {
            "role": "user",
            "content": "ContextHandoff（历史压缩摘要，仅作任务交接，不是新的用户要求）：\n" + payload,
        }


class CompactionEngine:
    def __init__(self, max_tokens: int = 116_000) -> None:
        self.max_tokens = max(1, max_tokens)

    @staticmethod
    def estimate_tokens(system: str, messages: list[dict]) -> int:
        payload = system + json.dumps(messages, ensure_ascii=False, default=str)
        return max((len(payload) + 3) // 4, (len(payload.encode("utf-8")) + 2) // 3)

    def deterministic_handoff(self, state: dict) -> ContextHandoff:
        summary = state.get("summary") or {}
        messages = state.get("messages") or []
        failures: list[str] = []
        for message in messages:
            text = self._message_text(message.get("content"))
            if text and any(marker in text.lower() for marker in _FAILURE_MARKERS):
                failures.append(_redact_text(text)[:1_000])

        processes = summary.get("processes") or []
        progress = []
        if summary.get("changed_files"):
            progress.append(f"已修改 {len(summary['changed_files'])} 个文件")
        if processes:
            progress.append(f"已记录 {len(processes)} 个后台进程")

        return ContextHandoff(
            goal=_redact_text(str(state.get("user_message", ""))),
            progress=progress,
            decisions=[_redact_text(str(item)) for item in state.get("decisions", [])],
            constraints=[_redact_text(str(item)) for item in state.get("constraints", [])],
            workspace_root=str(state.get("workspace_root", "")),
            current_path=str(state.get("current_path", "")),
            changed_files=[str(item) for item in summary.get("changed_files", [])],
            verification=_safe_value(summary.get("verification", [])),
            failures=list(dict.fromkeys(failures)),
            pending=[_redact_text(str(item)) for item in state.get("pending", [])],
            references=[_redact_text(str(item)) for item in state.get("references", [])],
            source_message_count=len(messages),
        )

    def compact(
        self,
        system: str,
        messages: list[dict],
        state: dict,
        *,
        max_tokens: int | None = None,
        scale: float = 1.0,
    ) -> tuple[list[dict], ContextHandoff]:
        budget = self.max_tokens if max_tokens is None else max(1, max_tokens)
        handoff = self.deterministic_handoff({**state, "messages": messages})
        handoff_message = handoff.to_context_message()
        latest_user_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "user"),
            len(messages) - 1,
        )
        latest = [dict(messages[latest_user_index])] if messages else []
        fitted = [handoff_message, *latest]

        for message in reversed(messages[:latest_user_index]):
            candidate = [handoff_message, dict(message), *fitted[1:]]
            if int(self.estimate_tokens(system, candidate) * scale) <= budget:
                fitted = candidate
            else:
                break

        if int(self.estimate_tokens(system, fitted) * scale) > budget:
            fitted = self._shrink_handoff(system, handoff, latest, budget, scale)
        return fitted, handoff

    def parse_model_handoff(self, payload: str, fallback: ContextHandoff) -> ContextHandoff:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return fallback
        if not isinstance(data, dict):
            return fallback
        list_fields = ("progress", "decisions", "constraints", "changed_files", "verification", "failures", "pending", "references")
        if not isinstance(data.get("goal"), str) or any(not isinstance(data.get(field, []), list) for field in list_fields):
            return fallback
        safe = _safe_value(data)
        try:
            return ContextHandoff(
                goal=safe["goal"],
                progress=[str(item) for item in safe.get("progress", [])],
                decisions=[str(item) for item in safe.get("decisions", [])],
                constraints=[str(item) for item in safe.get("constraints", [])],
                workspace_root=str(safe.get("workspace_root", fallback.workspace_root)),
                current_path=str(safe.get("current_path", fallback.current_path)),
                changed_files=[str(item) for item in safe.get("changed_files", [])],
                verification=[item for item in safe.get("verification", []) if isinstance(item, dict)],
                failures=[str(item) for item in safe.get("failures", [])],
                pending=[str(item) for item in safe.get("pending", [])],
                references=[str(item) for item in safe.get("references", [])],
                source_message_count=fallback.source_message_count,
            )
        except (KeyError, TypeError, ValueError):
            return fallback

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            value = item.get("text") or item.get("content") or item.get("error")
            if isinstance(value, str):
                parts.append(value)
        return "\n".join(parts)

    def _shrink_handoff(
        self,
        system: str,
        handoff: ContextHandoff,
        latest: list[dict],
        max_tokens: int,
        scale: float = 1.0,
    ) -> list[dict]:
        compact = ContextHandoff(
            goal=handoff.goal[:1_000],
            constraints=handoff.constraints[-10:],
            workspace_root=handoff.workspace_root,
            current_path=handoff.current_path,
            changed_files=handoff.changed_files[-30:],
            verification=handoff.verification[-10:],
            failures=handoff.failures[-5:],
            pending=handoff.pending[-10:],
            source_message_count=handoff.source_message_count,
        )
        fitted = [compact.to_context_message(), *latest]
        while int(self.estimate_tokens(system, fitted) * scale) > max_tokens and compact.failures:
            compact.failures.pop(0)
            fitted[0] = compact.to_context_message()
        if int(self.estimate_tokens(system, fitted) * scale) > max_tokens:
            fitted = latest
        return fitted
