"""
记忆系统 - 使用SQLite存储对话历史、项目状态、用户偏好
"""
import sqlite3
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

DB_PATH = Path(__file__).parent.parent.parent / "data" / "memory.db"

_EVENT_SECRET_KEY_MARKERS = ("key", "token", "secret", "password", "authorization")
_EVENT_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens"}
_EVENT_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
)


def _sanitize_event(value):
    if isinstance(value, str):
        result = value
        for pattern in _EVENT_SECRET_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if (
                str(key).lower() not in _EVENT_USAGE_KEYS
                and any(marker in str(key).lower() for marker in _EVENT_SECRET_KEY_MARKERS)
            )
            else _sanitize_event(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_event(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_event(item) for item in value]
    return value


class Memory:
    """Agent记忆系统"""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        cursor = self.conn.cursor()

        # 对话历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                repo TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 项目状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                repo TEXT PRIMARY KEY,
                status TEXT DEFAULT 'discovered',
                local_path TEXT,
                env_configured BOOLEAN DEFAULT 0,
                last_run TEXT,
                notes TEXT,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 操作日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target TEXT,
                details TEXT,
                success BOOLEAN,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 用户偏好表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                session_id TEXT PRIMARY KEY,
                root TEXT NOT NULL,
                current_path TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(workspaces)").fetchall()}
        if "current_path" not in columns:
            cursor.execute("ALTER TABLE workspaces ADD COLUMN current_path TEXT")
        cursor.execute("UPDATE workspaces SET current_path = root WHERE current_path IS NULL")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                status TEXT NOT NULL,
                state_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_requirements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                source_task_id TEXT NOT NULL,
                completed_task_id TEXT,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, position),
                UNIQUE(session_id, normalized_text)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_requirements_status "
            "ON session_requirements(session_id, status, position)"
        )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_tool_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_artifacts (
                artifact_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_artifacts_task ON agent_artifacts(task_id, created_at)"
        )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                input_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_kind TEXT,
                recovery_key TEXT,
                recovered_by_call_id TEXT,
                recovered_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                settled_at DATETIME,
                UNIQUE(task_id, call_id)
            )
        """)
        tool_call_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(agent_tool_calls)").fetchall()
        }
        for column, definition in (
            ("recovery_key", "TEXT"),
            ("recovered_by_call_id", "TEXT"),
            ("recovered_at", "DATETIME"),
        ):
            if column not in tool_call_columns:
                cursor.execute(f"ALTER TABLE agent_tool_calls ADD COLUMN {column} {definition}")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_changesets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                files_json TEXT NOT NULL,
                diff TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_id, sequence)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_root TEXT NOT NULL,
                content TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                verification_status TEXT NOT NULL DEFAULT 'unverified',
                expires_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(workspace_root, source_type, source_ref)
            )
        """)
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS project_memory_fts USING fts5(
                content,
                workspace_root UNINDEXED,
                memory_id UNINDEXED,
                tokenize = 'unicode61'
            )
        """)

        self.conn.commit()

    # ========== 对话历史 ==========

    def add_message(self, session_id: str, role: str, content: str, repo: str = None):
        """添加对话消息"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (session_id, role, content, repo) VALUES (?, ?, ?, ?)",
            (session_id, role, content, repo)
        )
        self.conn.commit()

    def get_history(self, session_id: str, limit: int = 20) -> List[Dict]:
        """获取对话历史"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT role, content, repo, timestamp FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)
        )
        rows = cursor.fetchall()
        return [{"role": r[0], "content": r[1], "repo": r[2], "timestamp": r[3]} for r in reversed(rows)]

    def get_repo_history(self, repo: str, limit: int = 50) -> List[Dict]:
        """获取特定项目的对话历史"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT role, content, timestamp FROM conversations WHERE repo = ? ORDER BY id DESC LIMIT ?",
            (repo, limit)
        )
        rows = cursor.fetchall()
        return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]

    # ========== 项目状态 ==========

    def update_project(self, repo: str, **kwargs):
        """更新项目状态"""
        cursor = self.conn.cursor()

        # 检查是否存在
        cursor.execute("SELECT repo FROM projects WHERE repo = ?", (repo,))
        exists = cursor.fetchone()

        if exists:
            updates = ", ".join(f"{k} = ?" for k in kwargs.keys())
            values = list(kwargs.values()) + [repo]
            cursor.execute(f"UPDATE projects SET {updates}, last_updated = CURRENT_TIMESTAMP WHERE repo = ?", values)
        else:
            kwargs["repo"] = repo
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join(["?"] * len(kwargs))
            cursor.execute(f"INSERT INTO projects ({cols}) VALUES ({placeholders})", list(kwargs.values()))

        self.conn.commit()

    def get_project(self, repo: str) -> Optional[Dict]:
        """获取项目状态"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM projects WHERE repo = ?", (repo,))
        row = cursor.fetchone()
        if not row:
            return None

        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    def get_all_projects(self) -> List[Dict]:
        """获取所有项目"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM projects ORDER BY last_updated DESC")
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ========== 操作日志 ==========

    def log_action(self, session_id: str, action_type: str, target: str, details: str, success: bool):
        """记录操作日志"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO action_logs (session_id, action_type, target, details, success) VALUES (?, ?, ?, ?, ?)",
            (session_id, action_type, target, details, success)
        )
        self.conn.commit()

    def get_action_logs(self, repo: str = None, limit: int = 20) -> List[Dict]:
        """获取操作日志"""
        cursor = self.conn.cursor()
        if repo:
            cursor.execute(
                "SELECT * FROM action_logs WHERE target = ? ORDER BY id DESC LIMIT ?",
                (repo, limit)
            )
        else:
            cursor.execute("SELECT * FROM action_logs ORDER BY id DESC LIMIT ?", (limit,))

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, r)) for r in rows]

    # ========== 会话工作区 ==========

    def set_workspace(self, session_id: str, root: str, current_path: str | None = None):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO workspaces (session_id, root, current_path, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(session_id) DO UPDATE SET
                   root = excluded.root,
                   current_path = excluded.current_path,
                   updated_at = CURRENT_TIMESTAMP""",
            (session_id, root, current_path or root),
        )
        self.conn.commit()

    def get_workspace(self, session_id: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT root FROM workspaces WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_workspace_state(self, session_id: str) -> Optional[Dict[str, str]]:
        row = self.conn.execute(
            "SELECT root, current_path FROM workspaces WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return {"root": row[0], "current_path": row[1] or row[0]}

    def set_current_path(self, session_id: str, current_path: str):
        self.conn.execute(
            "UPDATE workspaces SET current_path = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (current_path, session_id),
        )
        self.conn.commit()

    def get_recent_workspaces(self, limit: int = 8) -> List[str]:
        """Return unique workspace roots ordered by their most recent binding."""
        rows = self.conn.execute(
            """SELECT root, MAX(updated_at) AS last_used, MAX(rowid) AS last_row
               FROM workspaces
               GROUP BY root
               ORDER BY last_used DESC, last_row DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [row[0] for row in rows]

    # ========== 会话需求账本 ==========

    @staticmethod
    def _normalize_requirement_text(text: str) -> str:
        return " ".join(str(text).strip().split()).casefold()

    def merge_session_requirements(
        self,
        session_id: str,
        requirements: List[str],
        *,
        source_task_id: str,
    ) -> List[Dict]:
        normalized_items = []
        seen = set()
        for text in requirements:
            clean_text = " ".join(str(text).strip().split())
            normalized = self._normalize_requirement_text(clean_text)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_items.append((clean_text, normalized))

        next_position = self.conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM session_requirements WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        for text, normalized in normalized_items:
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO session_requirements
                   (session_id, position, text, normalized_text, source_task_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, next_position, text, normalized, source_task_id),
            )
            if cursor.rowcount:
                next_position += 1
        self.conn.commit()

        by_normalized = {
            item["normalized_text"]: item
            for item in self.list_session_requirements(session_id)
        }
        return [
            by_normalized[normalized]
            for _, normalized in normalized_items
            if normalized in by_normalized
        ]

    def list_session_requirements(
        self,
        session_id: str,
        *,
        status: str | None = None,
    ) -> List[Dict]:
        query = (
            "SELECT position, text, normalized_text, status, source_task_id, "
            "completed_task_id, evidence_json, created_at, updated_at "
            "FROM session_requirements WHERE session_id = ?"
        )
        params: list = [session_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY position"
        rows = self.conn.execute(query, params).fetchall()
        columns = (
            "position", "text", "normalized_text", "status", "source_task_id",
            "completed_task_id", "evidence", "created_at", "updated_at",
        )
        items = []
        for row in rows:
            item = dict(zip(columns, row))
            item["evidence"] = json.loads(item["evidence"] or "[]")
            items.append(item)
        return items

    def settle_session_requirements(
        self,
        session_id: str,
        task_id: str,
        results: List[Dict],
    ) -> None:
        for result in results:
            position = int(result["id"])
            passed = result.get("status") == "passed"
            self.conn.execute(
                """UPDATE session_requirements SET
                       status = CASE WHEN ? THEN 'completed' ELSE status END,
                       completed_task_id = CASE WHEN ? THEN ? ELSE completed_task_id END,
                       evidence_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE session_id = ? AND position = ?""",
                (
                    passed,
                    passed,
                    task_id,
                    json.dumps(result.get("evidence", []), ensure_ascii=False),
                    session_id,
                    position,
                ),
            )
        self.conn.commit()

    # ========== Agent 任务 ========== 

    def save_agent_task(self, state: Dict):
        self.conn.execute(
            """INSERT INTO agent_tasks (task_id, session_id, user_message, status, state_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                   session_id = excluded.session_id,
                   user_message = excluded.user_message,
                   status = excluded.status,
                   state_json = excluded.state_json,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                state["task_id"], state["session_id"], state.get("user_message", ""),
                state["status"], json.dumps(state, ensure_ascii=False, default=str),
            ),
        )
        self.conn.commit()

    def get_agent_task(self, task_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT state_json FROM agent_tasks WHERE task_id = ?", (task_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_latest_agent_task(self, session_id: str, status: str | None = None) -> Optional[Dict]:
        if status is None:
            row = self.conn.execute(
                "SELECT state_json FROM agent_tasks WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT state_json FROM agent_tasks WHERE session_id = ? AND status = ? ORDER BY updated_at DESC LIMIT 1",
                (session_id, status),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def record_agent_tool_run(self, task_id: str, tool_name: str, args: Dict, result: Dict):
        self.conn.execute(
            "INSERT INTO agent_tool_runs (task_id, tool_name, args_json, result_json) VALUES (?, ?, ?, ?)",
            (task_id, tool_name, json.dumps(args, ensure_ascii=False), json.dumps(result, ensure_ascii=False, default=str)),
        )
        self.conn.commit()

    def record_agent_artifact(
        self,
        *,
        task_id: str,
        call_id: str,
        tool_name: str,
        content: str,
        mime_type: str,
    ) -> Dict:
        artifact_id = uuid.uuid4().hex
        size = len(content.encode("utf-8"))
        self.conn.execute(
            """INSERT INTO agent_artifacts
               (artifact_id, task_id, call_id, tool_name, mime_type, size, content)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, task_id, call_id, tool_name, mime_type, size, content),
        )
        self.conn.commit()
        return self.get_agent_artifact(task_id, artifact_id)

    def get_agent_artifact(self, task_id: str, artifact_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            """SELECT artifact_id, task_id, call_id, tool_name, mime_type, size,
                      content, created_at
               FROM agent_artifacts WHERE task_id = ? AND artifact_id = ?""",
            (task_id, artifact_id),
        ).fetchone()
        if row is None:
            return None
        columns = (
            "artifact_id", "task_id", "call_id", "tool_name", "mime_type",
            "size", "content", "created_at",
        )
        return dict(zip(columns, row))

    def list_agent_artifacts(self, task_id: str) -> List[Dict]:
        rows = self.conn.execute(
            """SELECT artifact_id, task_id, call_id, tool_name, mime_type, size,
                      created_at
               FROM agent_artifacts WHERE task_id = ? ORDER BY created_at, artifact_id""",
            (task_id,),
        ).fetchall()
        columns = (
            "artifact_id", "task_id", "call_id", "tool_name", "mime_type",
            "size", "created_at",
        )
        return [dict(zip(columns, row)) for row in rows]

    def create_agent_tool_call(
        self,
        *,
        task_id: str,
        session_id: str,
        call_id: str,
        batch_id: str,
        tool_name: str,
        input: Dict,
        recovery_key: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO agent_tool_calls
               (task_id, session_id, call_id, batch_id, tool_name, input_json, status, recovery_key)
               VALUES (?, ?, ?, ?, ?, ?, 'parsed', ?)
               ON CONFLICT(task_id, call_id) DO NOTHING""",
            (
                task_id, session_id, call_id, batch_id, tool_name,
                json.dumps(input, ensure_ascii=False), recovery_key,
            ),
        )
        self.conn.commit()

    def get_agent_tool_calls(self, task_id: str) -> List[Dict]:
        rows = self.conn.execute(
            """SELECT session_id, call_id, batch_id, tool_name, input_json, status,
                      result_json, error_kind, recovery_key, recovered_by_call_id,
                      created_at, updated_at, settled_at, recovered_at
               FROM agent_tool_calls WHERE task_id = ? ORDER BY id""",
            (task_id,),
        ).fetchall()
        columns = (
            "session_id", "call_id", "batch_id", "tool_name", "input", "status",
            "result", "error_kind", "recovery_key", "recovered_by_call_id",
            "created_at", "updated_at", "settled_at", "recovered_at",
        )
        calls = []
        for row in rows:
            values = list(row)
            values[4] = json.loads(values[4])
            values[6] = json.loads(values[6]) if values[6] is not None else None
            calls.append({"task_id": task_id, **dict(zip(columns, values))})
        return calls

    def transition_agent_tool_call(
        self,
        task_id: str,
        call_id: str,
        status: str,
        *,
        result: Dict | None = None,
        error_kind: str | None = None,
    ) -> Dict:
        terminal = {"succeeded", "failed", "rejected", "interrupted"}
        transitions = {
            "parsed": {"awaiting_approval", "running", "failed", "interrupted"},
            "awaiting_approval": {"running", "rejected", "interrupted"},
            "running": {"succeeded", "failed", "interrupted"},
        }
        cursor = self.conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            row = cursor.execute(
                "SELECT status FROM agent_tool_calls WHERE task_id = ? AND call_id = ?",
                (task_id, call_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"工具调用不存在: {task_id}/{call_id}")
            current = row[0]
            if current in terminal:
                if current != status:
                    raise ValueError(f"工具调用终态不能从 {current} 改为 {status}")
                self.conn.commit()
                return next(call for call in self.get_agent_tool_calls(task_id) if call["call_id"] == call_id)
            if status not in transitions.get(current, set()):
                raise ValueError(f"非法工具调用状态转换: {current} -> {status}")
            cursor.execute(
                """UPDATE agent_tool_calls SET status = ?, result_json = ?, error_kind = ?,
                          updated_at = CURRENT_TIMESTAMP,
                          settled_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
                   WHERE task_id = ? AND call_id = ?""",
                (
                    status,
                    json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                    error_kind,
                    1 if status in terminal else 0,
                    task_id,
                    call_id,
                ),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return next(call for call in self.get_agent_tool_calls(task_id) if call["call_id"] == call_id)

    def mark_agent_tool_call_recovered(
        self,
        task_id: str,
        failed_call_id: str,
        recovered_by_call_id: str,
    ) -> Dict:
        cursor = self.conn.execute(
            """UPDATE agent_tool_calls
               SET recovered_by_call_id = ?, recovered_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE task_id = ? AND call_id = ? AND status = 'failed'
                 AND recovered_by_call_id IS NULL""",
            (recovered_by_call_id, task_id, failed_call_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"失败调用不能标记为已恢复: {task_id}/{failed_call_id}")
        self.conn.commit()
        return next(
            call for call in self.get_agent_tool_calls(task_id)
            if call["call_id"] == failed_call_id
        )

    def settle_open_agent_tool_calls(
        self,
        task_id: str,
        *,
        error: str,
        error_kind: str = "interrupted",
    ) -> List[Dict]:
        settled = []
        for call in self.get_agent_tool_calls(task_id):
            if call["status"] in {"parsed", "awaiting_approval", "running"}:
                settled.append(self.transition_agent_tool_call(
                    task_id,
                    call["call_id"],
                    "interrupted",
                    result={"success": False, "error": error, "error_kind": error_kind},
                    error_kind=error_kind,
                ))
        return settled

    def record_agent_changeset(self, task_id: str, files: List[str], diff: str):
        self.conn.execute(
            "INSERT INTO agent_changesets (task_id, files_json, diff) VALUES (?, ?, ?)",
            (task_id, json.dumps(files, ensure_ascii=False), diff),
        )
        self.conn.commit()

    def record_agent_event(self, event: Dict) -> int:
        """Append one redacted event and return its task-local sequence number."""
        task_id = str(event.get("task_id", ""))
        session_id = str(event.get("session_id", ""))
        event_type = str(event.get("type", "event"))
        payload = _sanitize_event({
            key: value
            for key, value in event.items()
            if key not in {"task_id", "session_id", "type"}
        })
        cursor = self.conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            row = cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            sequence = int(row[0])
            cursor.execute(
                """INSERT INTO agent_events
                   (task_id, session_id, sequence, event_type, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    task_id,
                    session_id,
                    sequence,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return sequence

    def get_agent_events(self, task_id: str, limit: int | None = None) -> List[Dict]:
        query = (
            "SELECT sequence, session_id, event_type, payload_json, created_at "
            "FROM agent_events WHERE task_id = ? ORDER BY sequence"
        )
        params: list = [task_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [
            {
                "task_id": task_id,
                "session_id": row[1],
                "sequence": row[0],
                "type": row[2],
                "payload": json.loads(row[3]),
                "created_at": row[4],
            }
            for row in rows
        ]

    def remember_project_fact(
        self,
        *,
        workspace_root: str,
        content: str,
        source_type: str,
        source_ref: str,
        confidence: float = 0.5,
        verification_status: str = "unverified",
        expires_at: str | None = None,
    ) -> int:
        """Upsert one provenance-bound project fact and synchronize its FTS row."""
        safe_content = str(_sanitize_event(content)).strip()
        cursor = self.conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            cursor.execute(
                """INSERT INTO project_memories
                   (workspace_root, content, source_type, source_ref, confidence,
                    verification_status, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_root, source_type, source_ref) DO UPDATE SET
                       content = excluded.content,
                       confidence = excluded.confidence,
                       verification_status = excluded.verification_status,
                       expires_at = excluded.expires_at,
                       updated_at = CURRENT_TIMESTAMP""",
                (
                    workspace_root,
                    safe_content,
                    source_type,
                    source_ref,
                    min(1.0, max(0.0, float(confidence))),
                    verification_status,
                    expires_at,
                ),
            )
            memory_id = int(cursor.execute(
                """SELECT id FROM project_memories
                   WHERE workspace_root = ? AND source_type = ? AND source_ref = ?""",
                (workspace_root, source_type, source_ref),
            ).fetchone()[0])
            cursor.execute("DELETE FROM project_memory_fts WHERE memory_id = ?", (memory_id,))
            cursor.execute(
                "INSERT INTO project_memory_fts (content, workspace_root, memory_id) VALUES (?, ?, ?)",
                (safe_content, workspace_root, memory_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return memory_id

    def search_project_memories(
        self,
        workspace_root: str,
        query: str,
        *,
        limit: int = 8,
        verified_only: bool = False,
    ) -> List[Dict]:
        terms = re.findall(r"[\w\u3400-\u9fff.-]+", query, flags=re.UNICODE)
        if not terms:
            return []
        match_query = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:12])
        verified_filter = "AND memories.verification_status = 'verified'" if verified_only else ""
        rows = self.conn.execute(
            f"""SELECT memories.id, memories.workspace_root, memories.content,
                       memories.source_type, memories.source_ref, memories.confidence,
                       memories.verification_status, memories.expires_at,
                       memories.created_at, memories.updated_at,
                       bm25(project_memory_fts) AS rank
                FROM project_memory_fts
                JOIN project_memories AS memories
                  ON memories.id = CAST(project_memory_fts.memory_id AS INTEGER)
                WHERE project_memory_fts MATCH ?
                  AND memories.workspace_root = ?
                  AND (memories.expires_at IS NULL OR memories.expires_at > CURRENT_TIMESTAMP)
                  {verified_filter}
                ORDER BY rank, memories.confidence DESC, memories.updated_at DESC
                LIMIT ?""",
            (match_query, workspace_root, max(1, min(int(limit), 50))),
        ).fetchall()
        columns = (
            "id", "workspace_root", "content", "source_type", "source_ref",
            "confidence", "verification_status", "expires_at", "created_at", "updated_at", "rank",
        )
        return [dict(zip(columns, row)) for row in rows]

    def get_agent_task_activity(self, task_id: str) -> Dict:
        tool_rows = self.conn.execute(
            "SELECT tool_name, args_json, result_json, created_at FROM agent_tool_runs WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        change_rows = self.conn.execute(
            "SELECT files_json, diff, created_at FROM agent_changesets WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        recovery_links = {
            call["call_id"]: call.get("recovered_by_call_id")
            for call in self.get_agent_tool_calls(task_id)
            if call.get("recovered_by_call_id")
        }
        tool_runs = []
        for row in tool_rows:
            result = json.loads(row[2])
            call_id = str(result.get("call_id") or "")
            tool_runs.append({
                "tool_name": row[0],
                "args": json.loads(row[1]),
                "result": result,
                "recovered_by_call_id": recovery_links.get(call_id),
                "created_at": row[3],
            })
        return {
            "events": self.get_agent_events(task_id),
            "tool_runs": tool_runs,
            "artifacts": self.list_agent_artifacts(task_id),
            "changesets": [
                {"files": json.loads(row[0]), "diff": row[1], "created_at": row[2]}
                for row in change_rows
            ],
        }

    def list_agent_traces(self, limit: int = 50) -> List[Dict]:
        """Build compact, local task summaries from persisted agent facts."""
        rows = self.conn.execute(
            """SELECT task_id, session_id, user_message, status, state_json,
                      created_at, updated_at
               FROM agent_tasks
               ORDER BY updated_at DESC, rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        traces = []
        for task_id, session_id, message, status, state_json, created_at, updated_at in rows:
            state = json.loads(state_json)
            tool_rows = self.conn.execute(
                "SELECT status, recovered_by_call_id FROM agent_tool_calls WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            if tool_rows:
                tool_count = len(tool_rows)
                failed_tools = sum(
                    1 for tool_status, recovered_by in tool_rows
                    if tool_status in {"failed", "rejected", "interrupted"} and not recovered_by
                )
                recovered_tools = sum(1 for _, recovered_by in tool_rows if recovered_by)
            else:
                legacy_rows = self.conn.execute(
                    "SELECT result_json FROM agent_tool_runs WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
                tool_count = len(legacy_rows)
                failed_tools = 0
                for (result_json,) in legacy_rows:
                    try:
                        if not json.loads(result_json).get("success", False):
                            failed_tools += 1
                    except (TypeError, json.JSONDecodeError):
                        failed_tools += 1
                recovered_tools = 0
            changed_files = set()
            for (files_json,) in self.conn.execute(
                "SELECT files_json FROM agent_changesets WHERE task_id = ?", (task_id,)
            ).fetchall():
                changed_files.update(json.loads(files_json))
            checks = state.get("summary", {}).get("verification", [])
            verification = "not_run"
            if checks:
                verification = "passed" if all(check.get("success", False) for check in checks) else "failed"
            traces.append({
                "task_id": task_id,
                "session_id": session_id,
                "message": message,
                "message_encoding_status": (
                    "legacy_corrupted" if re.search(r"\?{6,}", str(message)) else "intact"
                ),
                "status": status,
                "tool_count": tool_count,
                "failed_tool_count": failed_tools,
                "recovered_tool_count": recovered_tools,
                "changed_file_count": len(changed_files),
                "verification": verification,
                "created_at": created_at,
                "updated_at": updated_at,
            })
        return traces

    # ========== 用户偏好 ==========

    def set_preference(self, key: str, value: str):
        """设置用户偏好"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value)
        )
        self.conn.commit()

    def get_preference(self, key: str) -> Optional[str]:
        """获取用户偏好"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM user_preferences WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None

    def close(self):
        """关闭连接"""
        self.conn.close()


# 全局实例
memory = Memory()
