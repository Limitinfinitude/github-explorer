"""
记忆系统 - 使用SQLite存储对话历史、项目状态、用户偏好
"""
import sqlite3
import json
import re
import uuid
from datetime import datetime, timedelta
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
            CREATE TABLE IF NOT EXISTS agent_task_metrics (
                task_id TEXT PRIMARY KEY,
                metrics_version TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                computed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_task_metrics_version "
            "ON agent_task_metrics(metrics_version, computed_at)"
        )
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
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                msg_time TEXT,
                thinking_json TEXT NOT NULL DEFAULT '[]',
                narrations_json TEXT NOT NULL DEFAULT '[]',
                steps_json TEXT NOT NULL DEFAULT '[]',
                cmd_blocks_json TEXT NOT NULL DEFAULT '[]',
                agent_run_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id)"
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

    def get_session_requirement_context(
        self,
        session_id: str,
        *,
        pending_limit: int = 24,
        recent_completed_limit: int = 8,
    ) -> Dict:
        """Return a bounded prompt view without discarding the full ledger."""
        pending = self.list_session_requirements(session_id, status="pending")
        completed = self.list_session_requirements(session_id, status="completed")
        limit = max(1, int(pending_limit))
        return {
            "pending": pending[:limit],
            "pending_total": len(pending),
            "pending_truncated": len(pending) > limit,
            "recent_completed": completed[-max(0, int(recent_completed_limit)):],
        }

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
        if state.get("status") in {"completed", "incomplete", "failed", "blocked", "cancelled", "interrupted"}:
            self.refresh_agent_metrics_snapshot(state["task_id"])

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

    def get_latest_nonterminal_agent_task(self, session_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            """SELECT state_json FROM agent_tasks
               WHERE session_id = ?
                 AND status IN ('pending', 'queued', 'running', 'waiting_approval')
               ORDER BY updated_at DESC, rowid DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_previous_agent_task(
        self,
        session_id: str,
        *,
        exclude_task_id: str,
    ) -> Optional[Dict]:
        row = self.conn.execute(
            """SELECT state_json FROM agent_tasks
               WHERE session_id = ? AND task_id != ?
               ORDER BY updated_at DESC, rowid DESC LIMIT 1""",
            (session_id, exclude_task_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def prune_agent_events(self, days: int = 30) -> Dict[str, int]:
        """清理超过 days 天的运行时事件记录，防止 SQLite 无限膨胀。

        保留 agent_tasks（任务状态与回放仍需要），只清理事件流水、
        工具运行记录与操作日志。created_at 为 UTC 的 'YYYY-MM-DD HH:MM:SS'。
        """
        from datetime import datetime, timedelta

        cutoff = (datetime.utcnow() - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
        counts: Dict[str, int] = {}
        for table in ("agent_events", "agent_tool_runs", "action_logs"):
            try:
                counts[table] = self.conn.execute(
                    f"DELETE FROM {table} WHERE created_at < ?", (cutoff,)
                ).rowcount
            except sqlite3.OperationalError:
                counts[table] = 0
        self.conn.commit()
        return counts

    def reconcile_interrupted_runtime(self) -> List[str]:
        """Settle task and process snapshots that cannot survive a process restart."""
        rows = self.conn.execute(
            "SELECT task_id, state_json FROM agent_tasks WHERE status IN ('queued', 'running', 'pending')"
        ).fetchall()
        reconciled: List[str] = []
        for task_id, state_json in rows:
            state = json.loads(state_json)
            # pending 任务（注册后未启动）重启后不可恢复，直接中断
            if state.get("status") == "pending":
                state["status"] = "interrupted"
                state["resume_available"] = False
                state["resume_reason"] = "task_never_started"
            else:
                state["status"] = "interrupted"
                state["resume_available"] = True
                state["resume_reason"] = "runtime_restarted"
            state["resume_count"] = int(state.get("resume_count", 0))
            state["final_text"] = "服务重启前任务未结束，已标记为中断。"
            summary = state.setdefault("summary", {})
            processes = summary.get("processes", [])
            for process in processes:
                if isinstance(process, dict) and process.get("status") == "running":
                    process["status"] = "orphaned"
            self.settle_open_agent_tool_calls(
                task_id,
                error="Runtime restarted before tool completion.",
            )
            self.save_agent_task(state)
            self.record_agent_event({
                "task_id": task_id,
                "session_id": state.get("session_id", ""),
                "type": "runtime_reconciled",
                "previous_status": "running",
                "status": "interrupted",
                "orphaned_process_ids": [
                    process.get("process_id")
                    for process in processes
                    if isinstance(process, dict) and process.get("status") == "orphaned"
                ],
            })
            reconciled.append(task_id)
        return reconciled

    def record_agent_tool_run(self, task_id: str, tool_name: str, args: Dict, result: Dict):
        self.conn.execute(
            "INSERT INTO agent_tool_runs (task_id, tool_name, args_json, result_json) VALUES (?, ?, ?, ?)",
            (task_id, tool_name, json.dumps(args, ensure_ascii=False), json.dumps(result, ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        self.invalidate_agent_metrics_snapshot(task_id)

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
        self.invalidate_agent_metrics_snapshot(task_id)
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
        self.invalidate_agent_metrics_snapshot(task_id)

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
        self.invalidate_agent_metrics_snapshot(task_id)
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
        self.invalidate_agent_metrics_snapshot(task_id)
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
        self.invalidate_agent_metrics_snapshot(task_id)

    def save_chat_message(self, session_id: str, message: Dict) -> None:
        """保存一条前端聊天消息（含思考/工具步骤等过程数据）。"""
        self.conn.execute(
            """INSERT INTO chat_messages
               (session_id, role, content, msg_time, thinking_json, narrations_json,
                steps_json, cmd_blocks_json, agent_run_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                str(message.get("role", "assistant")),
                str(message.get("content", "")),
                str(message.get("time", "")),
                json.dumps(message.get("thinking") or [], ensure_ascii=False, default=str),
                json.dumps(message.get("narrations") or [], ensure_ascii=False, default=str),
                json.dumps(message.get("steps") or [], ensure_ascii=False, default=str),
                json.dumps(message.get("cmdBlocks") or [], ensure_ascii=False, default=str),
                json.dumps(message.get("agentRun") or {}, ensure_ascii=False, default=str),
            ),
        )
        self.conn.commit()

    def get_chat_messages(self, session_id: str) -> List[Dict]:
        """读取一个会话的完整聊天消息（按保存顺序）。"""
        rows = self.conn.execute(
            """SELECT role, content, msg_time, thinking_json, narrations_json,
                      steps_json, cmd_blocks_json, agent_run_json
               FROM chat_messages WHERE session_id = ? ORDER BY id""",
            (session_id,),
        ).fetchall()
        messages: List[Dict] = []
        for role, content, msg_time, thinking_json, narrations_json, steps_json, cmd_blocks_json, agent_run_json in rows:
            messages.append({
                "role": role,
                "content": content,
                "time": msg_time or "",
                "thinking": json.loads(thinking_json or "[]"),
                "narrations": json.loads(narrations_json or "[]"),
                "steps": json.loads(steps_json or "[]"),
                "cmdBlocks": json.loads(cmd_blocks_json or "[]"),
                "agentRun": json.loads(agent_run_json or "{}"),
            })
        return messages

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
        if event_type in {
            "task_completed", "task_failed", "task_finished", "task_cancelled",
            "runtime_reconciled", "stage_budget_exhausted", "tool_repair_exhausted",
        }:
            self.refresh_agent_metrics_snapshot(task_id)
        return sequence

    def get_agent_events(self, task_id: str, limit: int | None = None, after_sequence: int = 0) -> List[Dict]:
        query = (
            "SELECT sequence, session_id, event_type, payload_json, created_at "
            "FROM agent_events WHERE task_id = ? AND sequence > ? ORDER BY sequence"
        )
        params: list = [task_id, max(0, int(after_sequence))]
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

    def get_agent_chat_history(self, session_id: str, limit: int = 20) -> List[Dict]:
        rows = self.conn.execute(
            """SELECT state_json, created_at, updated_at
               FROM agent_tasks
               WHERE session_id = ?
               ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (session_id, max(1, int(limit))),
        ).fetchall()
        history = []
        for state_json, created_at, updated_at in reversed(rows):
            state = json.loads(state_json)
            user_message = state.get("user_message")
            final_text = state.get("final_text")
            if isinstance(user_message, str) and user_message:
                history.append({"role": "user", "content": user_message, "repo": None, "timestamp": created_at})
            if isinstance(final_text, str) and final_text:
                history.append({"role": "assistant", "content": final_text, "repo": None, "timestamp": updated_at})
        return history[-max(1, int(limit)):]

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

    def delete_project_memories(self, workspace_root: str) -> int:
        """删除一个项目的全部记忆（评测隔离 / 用户清理）。同时清理 FTS 索引。"""
        rows = self.conn.execute(
            "SELECT id FROM project_memories WHERE workspace_root = ?", (workspace_root,)
        ).fetchall()
        ids = [row[0] for row in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(
            f"DELETE FROM project_memory_fts WHERE memory_id IN ({placeholders})", ids
        )
        self.conn.execute(
            f"DELETE FROM project_memories WHERE id IN ({placeholders})", ids
        )
        self.conn.commit()
        return len(ids)

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

    def list_project_memories(
        self,
        workspace_root: str,
        *,
        limit: int = 20,
        verified_only: bool = False,
    ) -> List[Dict]:
        """List all non-expired project facts without requiring FTS search terms."""
        verified_filter = "AND verification_status = 'verified'" if verified_only else ""
        rows = self.conn.execute(
            f"""SELECT id, workspace_root, content, source_type, source_ref,
                       confidence, verification_status, expires_at,
                       created_at, updated_at
                FROM project_memories
                WHERE workspace_root = ?
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                  {verified_filter}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?""",
            (workspace_root, max(1, min(int(limit), 100))),
        ).fetchall()
        columns = (
            "id", "workspace_root", "content", "source_type", "source_ref",
            "confidence", "verification_status", "expires_at", "created_at", "updated_at",
        )
        return [dict(zip(columns, row)) for row in rows]

    def invalidate_agent_metrics_snapshot(self, task_id: str) -> None:
        self.conn.execute("DELETE FROM agent_task_metrics WHERE task_id = ?", (task_id,))
        self.conn.commit()

    def get_agent_metrics_snapshot(self, task_id: str) -> Optional[Dict]:
        row = self.conn.execute(
            "SELECT metrics_version, metrics_json, computed_at "
            "FROM agent_task_metrics WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "task_id": task_id,
            "metrics_version": row[0],
            "metrics": json.loads(row[1]),
            "computed_at": row[2],
        }

    def save_agent_metrics_snapshot(self, task_id: str, metrics: Dict) -> Dict:
        from agent.runtime.metrics import METRICS_VERSION

        self.conn.execute(
            """INSERT INTO agent_task_metrics (task_id, metrics_version, metrics_json, computed_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(task_id) DO UPDATE SET
                   metrics_version = excluded.metrics_version,
                   metrics_json = excluded.metrics_json,
                   computed_at = CURRENT_TIMESTAMP""",
            (task_id, METRICS_VERSION, json.dumps(metrics, ensure_ascii=False, default=str)),
        )
        self.conn.commit()
        return metrics

    def get_agent_trace_activity(self, task_id: str) -> Dict:
        """Read only fields required for trace metrics; omit artifacts and diff bodies."""
        tool_rows = self.conn.execute(
            "SELECT tool_name, args_json, result_json, created_at "
            "FROM agent_tool_runs WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        tool_calls = self.get_agent_tool_calls(task_id)
        recovery_links = {
            call["call_id"]: call.get("recovered_by_call_id")
            for call in tool_calls
            if call.get("recovered_by_call_id")
        }
        tool_runs = []
        for tool_name, args_json, result_json, created_at in tool_rows:
            result = json.loads(result_json)
            call_id = str(result.get("call_id") or "")
            tool_runs.append({
                "tool_name": tool_name,
                "args": json.loads(args_json),
                "result": result,
                "recovered_by_call_id": recovery_links.get(call_id),
                "created_at": created_at,
            })
        if tool_calls:
            tool_runs = [
                {
                    "tool_name": call["tool_name"],
                    "args": call["input"],
                    "result": call.get("result") or {
                        "success": call.get("status") == "succeeded",
                        "call_id": call["call_id"],
                    },
                    "recovered_by_call_id": call.get("recovered_by_call_id"),
                    "created_at": call["created_at"],
                }
                for call in tool_calls
            ]
        change_rows = self.conn.execute(
            "SELECT files_json FROM agent_changesets WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return {
            "events": self.get_agent_events(task_id),
            "tool_runs": tool_runs,
            "changesets": [{"files": json.loads(row[0])} for row in change_rows],
        }

    def refresh_agent_metrics_snapshot(self, task_id: str) -> Optional[Dict]:
        state = self.get_agent_task(task_id)
        if state is None:
            return None
        from agent.runtime.metrics import calculate_task_metrics

        metrics = calculate_task_metrics(
            task=state,
            activity=self.get_agent_trace_activity(task_id),
        )
        self.save_agent_metrics_snapshot(task_id, metrics)
        return metrics

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

    def get_token_usage(self, days: int = 7, top: int = 10) -> Dict:
        """聚合模型调用 token 消耗：按天、按任务、按滑动窗口。

        数据源是 agent_events 的 model_request_completed（每次调用都带
        usage）；评测连跑时 5 小时限额（429）曾整轮覆没，消耗可视化是
        预算管理的前提。
        """
        cutoff = (datetime.utcnow() - timedelta(days=max(1, days))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = self.conn.execute(
            """SELECT task_id, payload_json, created_at FROM agent_events
               WHERE event_type = 'model_request_completed' AND created_at >= ?
               ORDER BY id""",
            (cutoff,),
        ).fetchall()

        def _usage_of(payload: str) -> tuple[int, int]:
            try:
                usage = (json.loads(payload) or {}).get("usage") or {}
            except json.JSONDecodeError:
                return 0, 0
            inp = int(usage.get("input_tokens") or 0)
            out = int(
                usage.get("output_tokens")
                or usage.get("completion_tokens")
                or 0
            )
            return inp, out

        by_day: Dict[str, Dict] = {}
        by_task: Dict[str, Dict] = {}
        total_in = total_out = calls = 0
        five_hour_in = five_hour_out = five_hour_calls = 0
        window_start = datetime.utcnow() - timedelta(hours=5)
        window_str = window_start.strftime("%Y-%m-%d %H:%M:%S")

        for task_id, payload, created in rows:
            inp, out = _usage_of(payload)
            # created_at 是 UTC；按天聚合换算本地时区，与用户控制台视角一致
            try:
                local_day = (
                    datetime.strptime(str(created), "%Y-%m-%d %H:%M:%S")
                    + timedelta(hours=8)
                ).strftime("%Y-%m-%d")
            except ValueError:
                local_day = str(created)[:10]
            day = local_day
            calls += 1
            total_in += inp
            total_out += out
            by_day.setdefault(day, {"input": 0, "output": 0, "calls": 0})
            by_day[day]["input"] += inp
            by_day[day]["output"] += out
            by_day[day]["calls"] += 1
            task_key = str(task_id or "unknown")
            by_task.setdefault(task_key, {"input": 0, "output": 0, "calls": 0})
            by_task[task_key]["input"] += inp
            by_task[task_key]["output"] += out
            by_task[task_key]["calls"] += 1
            if str(created) >= window_str:
                five_hour_calls += 1
                five_hour_in += inp
                five_hour_out += out

        top_tasks = sorted(
            by_task.items(),
            key=lambda kv: -(kv[1]["input"] + kv[1]["output"]),
        )[: max(1, top)]
        return {
            "window_days": max(1, days),
            "total": {
                "calls": calls,
                "input_tokens": total_in,
                "output_tokens": total_out,
                "total_tokens": total_in + total_out,
            },
            "last_5h": {
                "calls": five_hour_calls,
                "input_tokens": five_hour_in,
                "output_tokens": five_hour_out,
                "total_tokens": five_hour_in + five_hour_out,
            },
            "by_day": [
                {
                    "date": day,
                    **stats,
                    "total_tokens": stats["input"] + stats["output"],
                }
                for day, stats in sorted(by_day.items())
            ],
            "top_tasks": [
                {
                    "task_id": task_id,
                    **stats,
                    "total_tokens": stats["input"] + stats["output"],
                }
                for task_id, stats in top_tasks
            ],
        }

    def list_agent_traces(
        self,
        limit: int = 50,
        *,
        status: str | None = None,
        terminal_reason: str | None = None,
        completion_evidence: str | None = None,
        workspace: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> List[Dict]:
        """Build compact trace summaries from snapshots and lightweight raw facts."""
        from agent.runtime.metrics import METRICS_VERSION, calculate_task_metrics

        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if from_date:
            clauses.append("created_at >= ?")
            params.append(from_date)
        if to_date:
            clauses.append("created_at <= ?")
            params.append(to_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""SELECT task_id, session_id, user_message, status, state_json,
                      created_at, updated_at
               FROM agent_tasks
               {where}
               ORDER BY updated_at DESC, rowid DESC""",
            params,
        ).fetchall()
        traces = []
        terminal_statuses = {"completed", "incomplete", "failed", "blocked", "cancelled", "interrupted"}
        for task_id, session_id, message, task_status, state_json, created_at, updated_at in rows:
            state = json.loads(state_json)
            snapshot = self.get_agent_metrics_snapshot(task_id)
            if snapshot and snapshot["metrics_version"] == METRICS_VERSION:
                quality_metrics = snapshot["metrics"]
                metrics_version = snapshot["metrics_version"]
                computed_at = snapshot["computed_at"]
            else:
                quality_metrics = calculate_task_metrics(
                    task=state,
                    activity=self.get_agent_trace_activity(task_id),
                )
                metrics_version = METRICS_VERSION
                computed_at = None
                if task_status in terminal_statuses:
                    self.save_agent_metrics_snapshot(task_id, quality_metrics)
                    computed_at = self.get_agent_metrics_snapshot(task_id)["computed_at"]

            workspace_root = state.get("workspace_root") or ""
            if workspace and workspace_root != workspace:
                continue
            if terminal_reason and quality_metrics["terminal_reason"] != terminal_reason:
                continue
            if completion_evidence and quality_metrics["completion_evidence"] != completion_evidence:
                continue
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
                "status": task_status,
                "resume_available": bool(state.get("resume_available")),
                "workspace_root": workspace_root,
                "tool_count": quality_metrics.get("tool_count", 0),
                "failed_tool_count": quality_metrics["unrecovered_tool_failures"],
                "recovered_tool_count": quality_metrics["recovered_tool_count"],
                "changed_file_count": quality_metrics.get("changed_file_count", 0),
                "verification": verification,
                "created_at": created_at,
                "updated_at": updated_at,
                "metrics_version": metrics_version,
                "metrics_computed_at": computed_at,
                "terminal_reason": quality_metrics["terminal_reason"],
                "completion_evidence": quality_metrics["completion_evidence"],
                "false_completion": quality_metrics["false_completion"],
                "false_incomplete": quality_metrics["false_incomplete"],
                "unrecovered_tool_failures": quality_metrics["unrecovered_tool_failures"],
                "budget_exhausted_stages": quality_metrics["budget_exhausted_stages"],
                "approval_count": quality_metrics["approval_count"],
                "successful_tool_count": quality_metrics["successful_tool_count"],
                "acceptance_passed": quality_metrics["acceptance_passed"],
                "acceptance_total": quality_metrics["acceptance_total"],
                "model_rounds": quality_metrics["model_rounds"],
                "model_error_count": quality_metrics.get("model_error_count", 0),
                "provider_truncation_count": quality_metrics.get("provider_truncation_count", 0),
                "model_latency_ms": quality_metrics["model_latency_ms"],
                "total_tokens": quality_metrics["total_tokens"],
                "event_count": quality_metrics["event_count"],
                "last_event_type": quality_metrics["last_event_type"],
                "last_event_at": quality_metrics["last_event_at"],
            })
            if len(traces) >= max(1, int(limit)):
                break
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
