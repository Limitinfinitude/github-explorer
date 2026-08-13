import json
import re
import uuid
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse


TERMINAL_TOOL_CALL_STATUSES = frozenset({
    "succeeded", "failed", "rejected", "interrupted",
})


def _normalized_path(value: str, current_path: Path) -> str:
    candidate = Path(value or ".").expanduser()
    if not candidate.is_absolute():
        candidate = current_path / candidate
    return str(candidate.resolve()).replace("\\", "/").casefold()


def _command_category(command: str) -> str:
    value = command.casefold()
    if re.search(r"\b(pytest|unittest|npm\s+(?:run\s+)?test|pnpm\s+test|yarn\s+test)\b", value):
        return "test"
    if re.search(r"\b(tsc|vite\s+build|npm\s+run\s+build|pnpm\s+build|yarn\s+build|compileall)\b", value):
        return "build"
    if re.search(r"\b(curl(?:\.exe)?|invoke-webrequest|invoke-restmethod)\b", value):
        return "http"
    if re.search(r"\b(python|pip|venv|conda|where(?:\.exe)?\s+python|get-location)\b", value):
        return "environment"
    if re.search(r"\bgit\b", value):
        return "git"
    if re.search(r"\b(npm|pnpm|yarn|node|npx)\b", value):
        return "node"
    return "generic"


def tool_recovery_key(name: str, args: dict, current_path: Path) -> str | None:
    if name == "run_command":
        cwd = _normalized_path(str(args.get("cwd", ".")), current_path)
        return f"command:{cwd}:{_command_category(str(args.get('command', '')))}"
    if name in {"ensure_venv", "install_dependencies", "verify_project", "detect_project"}:
        path = _normalized_path(str(args.get("path", ".")), current_path)
        phase = "environment" if name in {"ensure_venv", "install_dependencies"} else name
        return f"project:{path}:{phase}"
    if name in {"read_file", "list_directory", "search_text", "create_directory"}:
        path = _normalized_path(str(args.get("path", ".")), current_path)
        return f"path:{name}:{path}"
    if name == "edit_files":
        paths = sorted({
            _normalized_path(str(edit.get("path", ".")), current_path)
            for edit in args.get("edits", []) if isinstance(edit, dict)
        })
        return f"path:edit_files:{'|'.join(paths)}" if paths else None
    if name == "check_port":
        return f"network:{str(args.get('host', '')).casefold()}:{args.get('port')}"
    if name == "wait_http":
        parsed = urlparse(str(args.get("url", "")))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return f"network:{(parsed.hostname or '').casefold()}:{port}"
    if name in {"get_process", "stop_process"} and args.get("process_id"):
        return f"process:{args['process_id']}"
    if name == "start_process":
        cwd = _normalized_path(str(args.get("cwd", ".")), current_path)
        return f"process-start:{cwd}"
    return None


def _new_call_id(used: set[str]) -> str:
    while True:
        call_id = f"call_{uuid.uuid4().hex}"
        if call_id not in used:
            return call_id


def normalize_tool_calls(
    tool_uses: list[dict],
    existing_ids: set[str] | None = None,
) -> list[dict]:
    used = set(existing_ids or ())
    normalized = []
    for tool_use in tool_uses:
        source_call_id = str(tool_use.get("id") or "").strip()
        call_id = source_call_id
        if not call_id or call_id.startswith("text-tool-") or call_id in used:
            call_id = _new_call_id(used)
        used.add(call_id)
        normalized.append({
            **tool_use,
            "id": call_id,
            "source_call_id": source_call_id or None,
            "name": str(tool_use.get("name") or ""),
            "input": dict(tool_use.get("input") or {}),
        })
    return normalized


def interrupted_tool_result(call_id: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": json.dumps({
            "success": False,
            "error": "Tool call interrupted before completion. The task ended before a result was recorded.",
            "error_kind": "interrupted",
        }, ensure_ascii=False),
    }


def _ledger_tool_result(call_id: str, ledger: dict[str, dict]) -> dict:
    record = ledger.get(call_id) or {}
    if record.get("status") not in TERMINAL_TOOL_CALL_STATUSES:
        return interrupted_tool_result(call_id)
    result = record.get("result")
    if not isinstance(result, dict):
        result = {
            "success": record.get("status") == "succeeded",
            "error": None if record.get("status") == "succeeded" else "Tool call did not produce a result.",
        }
    if record.get("error_kind") and "error_kind" not in result:
        result = {**result, "error_kind": record["error_kind"]}
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": json.dumps(result, ensure_ascii=False, default=str),
    }


def reconcile_tool_messages(
    messages: list[dict],
    ledger: dict[str, dict] | None = None,
) -> list[dict]:
    records = ledger or {}
    safe: list[dict] = []
    index = 0
    while index < len(messages):
        message = deepcopy(messages[index])
        content = message.get("content")
        tool_uses = [
            block for block in content
            if isinstance(content, list) and isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if message.get("role") == "assistant" and tool_uses:
            safe.append(message)
            call_ids = [str(block.get("id") or "") for block in tool_uses]
            existing_results: dict[str, dict] = {}
            other_content: list = []
            next_index = index + 1
            if next_index < len(messages):
                candidate = messages[next_index]
                candidate_content = candidate.get("content")
                if candidate.get("role") == "user" and isinstance(candidate_content, list):
                    for block in candidate_content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            other_content.append(deepcopy(block))
                            continue
                        call_id = str(block.get("tool_use_id") or "")
                        if call_id in call_ids and call_id not in existing_results:
                            existing_results[call_id] = deepcopy(block)
                    index = next_index
            results = [
                existing_results.get(call_id) or _ledger_tool_result(call_id, records)
                for call_id in call_ids
            ]
            safe.append({"role": "user", "content": [*results, *other_content]})
            index += 1
            continue

        if message.get("role") == "user" and isinstance(content, list):
            cleaned = [
                block for block in content
                if not isinstance(block, dict) or block.get("type") != "tool_result"
            ]
            if cleaned:
                message["content"] = cleaned
                safe.append(message)
        else:
            safe.append(message)
        index += 1
    return safe
