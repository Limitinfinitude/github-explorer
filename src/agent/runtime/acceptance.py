import json
import ntpath
import re
from pathlib import Path


_BEHAVIOR_TERMS = (
    "搜索", "筛选", "过滤", "交互", "点击", "提交", "登录", "注册",
    "添加", "删除", "编辑", "上传", "下载", "排序", "分页",
)
_BEHAVIOR_CHECKS = {"unit", "browser"}
_ACTION_ALIASES = (
    ("搜索", "search"),
    ("筛选", "过滤", "filter"),
    ("点击", "click"),
    ("提交", "submit"),
    ("登录", "login", "sign in"),
    ("注册", "register", "sign up"),
    ("添加", "新增", "add", "create"),
    ("删除", "delete", "remove"),
    ("编辑", "修改", "edit", "update"),
    ("上传", "upload"),
    ("下载", "download"),
    ("排序", "sort"),
    ("分页", "pagination", "paginate"),
)


class WorkProductEvaluator:
    """Evaluate requirement evidence independently from the producing model."""

    _evidence_pattern = re.compile(
        r"\[(?:证据|evidence):(file|check|process):([^\]]+)\]",
        re.IGNORECASE,
    )
    _item_pattern = re.compile(
        r"(?ms)^\s*(?:[-*]\s*)?(?:\*\*)?\[?(\d{1,2})\]?\s*[.)、:]?\s*(.*?)"
        r"(?=^\s*(?:[-*]\s*)?(?:\*\*)?\[?\d{1,2}\]?\s*[.)、:]?\s*|\Z)",
    )

    def evaluate(
        self,
        *,
        criteria: list[dict],
        response_text: str,
        summary: dict,
    ) -> dict:
        raw_checks = [
            check for check in summary.get("verification", [])
            if isinstance(check, dict)
        ]
        checks = self._validate_verification_checks(raw_checks, str(summary.get("workspace_root", "")))
        passed_checks = sum(bool(check.get("success")) for check in checks)
        failed_checks = len(checks) - passed_checks
        technical_status = (
            "not_run" if not checks else "failed" if failed_checks else "passed"
        )
        evidence_summary = {**summary, "verification": checks}
        items = self._requirement_items(criteria, response_text, evidence_summary)
        technical_verification = {
            "status": technical_status,
            "passed": passed_checks,
            "failed": failed_checks,
            "total": len(checks),
        }
        if any(check.get("evidence_status") == "invalid" for check in checks):
            technical_verification["checks"] = checks
        return {
            "requirement_coverage": {
                "success": bool(items) and all(item["status"] == "passed" for item in items),
                "passed": sum(item["status"] == "passed" for item in items),
                "failed": sum(item["status"] == "failed" for item in items),
                "unverified": sum(item["status"] == "unverified" for item in items),
                "total": len(items),
                "items": items,
            },
            "technical_verification": technical_verification,
        }

    @staticmethod
    def _validate_verification_checks(checks: list[dict], workspace_root: str) -> list[dict]:
        """Cross-check provider claims against local project manifests."""
        root = Path(workspace_root) if workspace_root else None
        validated: list[dict] = []
        for check in checks:
            item = dict(check)
            item.setdefault("evidence_status", "verified" if item.get("success") else "failed")
            command = str(item.get("command") or "").strip()
            if item.get("success") and root and command:
                cwd = Path(str(item.get("cwd") or root))
                if not cwd.is_absolute():
                    cwd = root / cwd
                npm_script = None
                if command == "npm test":
                    npm_script = "test"
                elif command.startswith("npm run "):
                    npm_script = command.split(None, 2)[2].split()[0]
                if npm_script:
                    manifest = cwd / "package.json"
                    try:
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                        scripts = data.get("scripts") or {}
                    except (OSError, json.JSONDecodeError, TypeError):
                        scripts = {}
                    if npm_script not in scripts:
                        item["success"] = False
                        item["evidence_status"] = "invalid"
                        item["evidence_reason"] = f"package.json 未声明 scripts.{npm_script}"
                elif command.startswith("pytest"):
                    has_tests = any(
                        path.is_file() and (path.name.startswith("test_") or path.name.endswith("_test.py"))
                        for path in cwd.rglob("*.py")
                    ) if cwd.is_dir() else False
                    if not has_tests:
                        item["success"] = False
                        item["evidence_status"] = "invalid"
                        item["evidence_reason"] = "工作目录未发现 pytest 测试文件"
            validated.append(item)
        return validated

    def _requirement_items(
        self,
        criteria: list[dict],
        response_text: str,
        summary: dict,
    ) -> list[dict]:
        if not criteria:
            return []
        workspace_root = str(summary.get("workspace_root", ""))
        changed_files = {
            self._normalize_file_ref(path, workspace_root)
            for path in summary.get("changed_files", [])
        }
        successful_checks = {
            str(check.get("kind", "command")).casefold()
            for check in summary.get("verification", [])
            if isinstance(check, dict) and check.get("success", False)
        }
        process_ids = {
            str(process.get("process_id", "")).casefold()
            for process in summary.get("processes", [])
            if isinstance(process, dict) and process.get("process_id")
        }
        sections = {
            int(match.group(1)): match.group(2).strip()
            for match in self._item_pattern.finditer(response_text)
        }
        criterion_ids = [int(criterion["id"]) for criterion in criteria]
        if (
            sections
            and len(sections) == len(criteria)
            and list(sections) == list(range(1, len(criteria) + 1))
            and list(sections) != criterion_ids
        ):
            sections = dict(zip(criterion_ids, sections.values()))

        ledger = []
        for criterion in criteria:
            item_id = int(criterion["id"])
            criterion_text = str(criterion["text"])
            section = sections.get(item_id, "")
            evidence = []
            for match in self._evidence_pattern.finditer(section):
                evidence_type = match.group(1).casefold()
                evidence_ref = match.group(2).strip()
                normalized_ref = self._normalize_file_ref(evidence_ref, workspace_root)
                if evidence_type == "file":
                    valid = normalized_ref in changed_files
                elif evidence_type == "check":
                    valid = normalized_ref in successful_checks
                else:
                    valid = normalized_ref in process_ids
                sufficient = valid and self._is_sufficient(
                    criterion_text, evidence_type, normalized_ref,
                )
                item = {"type": evidence_type, "ref": evidence_ref, "valid": valid}
                if valid and not sufficient:
                    item["sufficient"] = False
                evidence.append(item)

            if (
                not evidence
                and "[完成]" in section
                and self._has_consistent_actions(criterion_text, section)
            ):
                evidence = self._automatic_evidence(
                    criterion_text,
                    changed_files=changed_files,
                    successful_checks=successful_checks,
                    process_ids=process_ids,
                )

            if "[未完成]" in section:
                status = "failed"
                reason = "回复明确标记为未完成"
            elif "[完成]" not in section:
                status = "unverified"
                reason = "缺少明确的完成状态"
            elif not any(item["valid"] for item in evidence):
                status = "unverified"
                reason = "完成声明缺少有效执行证据"
            elif not any(item.get("sufficient", True) for item in evidence if item["valid"]):
                status = "unverified"
                reason = "执行证据存在，但不足以证明该需求的功能语义"
            elif not self._has_consistent_actions(criterion_text, section):
                status = "unverified"
                reason = "完成声明与该需求的核心动作不一致"
            else:
                status = "passed"
                reason = ""
            ledger.append({
                "id": item_id,
                "text": criterion_text,
                "status": status,
                "evidence": evidence,
                "reason": reason,
            })
        return ledger

    @staticmethod
    def _automatic_evidence(
        criterion_text: str,
        *,
        changed_files: set[str],
        successful_checks: set[str],
        process_ids: set[str],
    ) -> list[dict]:
        """Use only persisted facts when a provider omits the evidence marker."""
        criterion = criterion_text.casefold()
        if process_ids and any(term in criterion for term in ("服务", "进程", "端口", "启动", "运行")):
            return [{"type": "process", "ref": sorted(process_ids)[0], "valid": True, "auto": True}]
        if any(term in criterion for term in _BEHAVIOR_TERMS):
            behavior_checks = sorted(successful_checks & _BEHAVIOR_CHECKS)
            if behavior_checks:
                return [{"type": "check", "ref": behavior_checks[0], "valid": True, "auto": True}]
        if changed_files:
            return [{"type": "file", "ref": sorted(changed_files)[0], "valid": True, "auto": True}]
        if successful_checks:
            return [{"type": "check", "ref": sorted(successful_checks)[0], "valid": True, "auto": True}]
        return []

    @staticmethod
    def _normalize_file_ref(path: object, workspace_root: str) -> str:
        normalized = ntpath.normpath(str(path))
        if workspace_root and ntpath.isabs(normalized):
            try:
                relative = ntpath.relpath(normalized, ntpath.normpath(workspace_root))
                if relative != ".." and not relative.startswith(f"..{ntpath.sep}"):
                    normalized = relative
            except ValueError:
                pass
        return normalized.replace("\\", "/").casefold()

    @staticmethod
    def _is_sufficient(criterion_text: str, evidence_type: str, evidence_ref: str) -> bool:
        is_behavior = any(term in criterion_text.casefold() for term in _BEHAVIOR_TERMS)
        if not is_behavior:
            return True
        return evidence_type == "check" and evidence_ref in _BEHAVIOR_CHECKS

    @staticmethod
    def _has_consistent_actions(criterion_text: str, section: str) -> bool:
        criterion = criterion_text.casefold()
        response = section.casefold()
        required_actions = [
            aliases for aliases in _ACTION_ALIASES
            if any(alias in criterion for alias in aliases)
        ]
        return all(
            any(alias in response for alias in aliases)
            for aliases in required_actions
        )
