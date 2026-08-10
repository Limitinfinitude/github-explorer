"""
记忆系统 - 使用SQLite存储对话历史、项目状态、用户偏好
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

DB_PATH = Path(__file__).parent.parent.parent / "data" / "memory.db"


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
            CREATE TABLE IF NOT EXISTS agent_changesets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                files_json TEXT NOT NULL,
                diff TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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

    def record_agent_changeset(self, task_id: str, files: List[str], diff: str):
        self.conn.execute(
            "INSERT INTO agent_changesets (task_id, files_json, diff) VALUES (?, ?, ?)",
            (task_id, json.dumps(files, ensure_ascii=False), diff),
        )
        self.conn.commit()

    def get_agent_task_activity(self, task_id: str) -> Dict:
        tool_rows = self.conn.execute(
            "SELECT tool_name, args_json, result_json, created_at FROM agent_tool_runs WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        change_rows = self.conn.execute(
            "SELECT files_json, diff, created_at FROM agent_changesets WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return {
            "tool_runs": [
                {"tool_name": row[0], "args": json.loads(row[1]), "result": json.loads(row[2]), "created_at": row[3]}
                for row in tool_rows
            ],
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
                "SELECT result_json FROM agent_tool_runs WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            failed_tools = 0
            for (result_json,) in tool_rows:
                try:
                    if not json.loads(result_json).get("success", False):
                        failed_tools += 1
                except (TypeError, json.JSONDecodeError):
                    failed_tools += 1
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
                "status": status,
                "tool_count": len(tool_rows),
                "failed_tool_count": failed_tools,
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
