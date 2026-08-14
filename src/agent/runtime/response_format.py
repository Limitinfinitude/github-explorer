import re
import json


_SECTION_RE = re.compile(r"^#{1,3}\s*(完成结果|文件变更|验证|运行状态)\s*$", re.MULTILINE)
_ENGLISH_META_RE = re.compile(
    r"^(?:the user asked|the task (?:is|was)|i (?:have )?(?:completed|read|checked)|done[.!]?)",
    re.IGNORECASE,
)


def _completion_text(text: str, summary: dict | None = None) -> str:
    cleaned = text.strip() or "任务已完成。"
    matches = list(_SECTION_RE.finditer(cleaned))
    if matches:
        first = matches[0]
        next_start = matches[1].start() if len(matches) > 1 else len(cleaned)
        prefix = cleaned[:first.start()].strip()
        body = cleaned[first.end():next_start].strip()
        cleaned = "\n\n".join(part for part in (prefix, body) if part) or "任务已完成。"

    # Some models append planning narration after the actual answer. Keep the
    # user-facing result and discard that internal meta commentary.
    meta_markers = (
        "现在我需要", "根据指令", "直接告诉用户", "我只需要", "用户明确要求",
        "用户说", "我应该", "我需要", "保持简洁", "这是一个简单的问候",
        "我应该给出", "完美！", "完美!",
    )
    positions = [cleaned.find(marker) for marker in meta_markers if cleaned.find(marker) >= 0]
    if positions:
        cleaned = cleaned[:min(positions)].strip()
    cleaned = re.sub(r"(?:读取成功|已读取)[^。！？\n]*[。！？]", "", cleaned).strip()
    summary_tail = re.search(
        r"(?:\n\s*)+(?:\*\*)?(?:文件变更|验证|运行状态|查看方式)(?:：|:)?(?:\*\*)?",
        cleaned,
    )
    if summary_tail:
        cleaned = cleaned[:summary_tail.start()].strip()

    # Tool arguments are an implementation detail. If a model echoes them as
    # its final answer, keep the real tool summary instead of exposing JSON.
    candidate = cleaned.strip().strip("`").strip()
    if "<tool_call>" in candidate or "<function=" in candidate or "DSML" in candidate:
        cleaned = _fact_summary(summary or {})
        candidate = cleaned
    if candidate[:1] in "[{":
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, (dict, list)) and _contains_tool_fields(payload):
            cleaned = _fact_summary(summary or {})

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    unique: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        if _ENGLISH_META_RE.match(paragraph.strip()):
            continue
        key = re.sub(r"\s+", " ", paragraph).strip().casefold()
        if key not in seen:
            seen.add(key)
            unique.append(paragraph)
    return "\n\n".join(unique) or "任务已完成。"


def _fact_summary(summary: dict) -> str:
    files = list(dict.fromkeys(summary.get("changed_files", [])))
    checks = [item for item in summary.get("verification", []) if isinstance(item, dict)]
    processes = summary.get("processes", [])
    parts = []
    if files:
        parts.append(f"已修改 {len(files)} 个文件")
    if checks:
        passed = sum(bool(check.get("success")) for check in checks)
        failed = len(checks) - passed
        parts.append(f"{passed} 项验证通过" + (f"，{failed} 项失败" if failed else ""))
    if processes:
        running = sum(process.get("status") == "running" for process in processes)
        parts.append(f"{running} 个后台进程运行中")
    return "，".join(parts) + "。" if parts else "任务未产生可确认的执行结果。"


def _acceptance_summary(items: list[dict]) -> str:
    labels = {"passed": "完成", "failed": "未完成", "unverified": "未验证"}
    lines = []
    for item in items:
        label = labels.get(str(item.get("status")), "未验证")
        reason = str(item.get("reason") or "").strip()
        suffix = f"（{reason}）" if reason else ""
        lines.append(f"{item.get('id')}. [{label}] {item.get('text', '')}{suffix}")
    return "\n".join(lines)


def _contains_tool_fields(value: object) -> bool:
    if isinstance(value, dict):
        if {"path", "operation"}.issubset(value):
            return True
        if "tool_use" in value or "tool_uses" in value:
            return True
        return any(_contains_tool_fields(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tool_fields(item) for item in value)
    return False


def format_final_response(text: str, summary: dict | None = None) -> str:
    summary = summary or {}
    files = list(dict.fromkeys(summary.get("changed_files", [])))
    checks = summary.get("verification", [])
    processes = summary.get("processes", [])
    acceptance = [
        item for item in summary.get("acceptance", [])
        if isinstance(item, dict)
    ]
    completion = (
        _acceptance_summary(acceptance)
        if not text.strip() and acceptance
        else _completion_text(text, summary)
    )

    if not files and not checks and not processes:
        return completion

    file_lines = [f"- `{path}`" for path in files] or ["- 无文件变更"]
    check_lines = []
    for check in checks:
        evidence = []
        if check.get("cwd"):
            evidence.append(f"cwd: `{check['cwd']}`")
        if check.get("python_executable"):
            evidence.append(f"Python: `{check['python_executable']}`")
        if check.get("returncode") is not None:
            evidence.append(f"退出码: {check['returncode']}")
        suffix = f"（{'；'.join(evidence)}）" if evidence else ""
        check_lines.append(
            f"- `{check.get('command') or check.get('path', '检查')}`："
            f"{'通过' if check.get('success') else '失败'}{suffix}"
        )
    if not check_lines:
        check_lines = ["- 未运行验证"]
    process_lines = []
    for process in processes:
        process_id = process.get("process_id", "未知")
        status = process.get("status", "unknown")
        suffix = f"，{process['url']}" if process.get("url") else ""
        process_lines.append(f"- `{process_id}`：{status}{suffix}")
    if not process_lines:
        process_lines = ["- 无后台进程"]

    return "\n\n".join([
        "## 完成结果\n" + completion,
        "## 文件变更\n" + "\n".join(file_lines),
        "## 验证\n" + "\n".join(check_lines),
        "## 运行状态\n" + "\n".join(process_lines),
    ])
