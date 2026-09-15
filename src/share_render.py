"""会话分享：把 chat_messages 渲染为单文件自包含 HTML（无外部资源，可直接打开或托管）。

- 输出包含：消息正文（极简 markdown）、工作过程时间线（思考/工具/命令/旁白交错）、
  工具输出与 unified diff（绿=新增/红=删除/灰=上下文，带行号）。
- 所有文本先 HTML 转义再套标签，防止会话内容注入。
- 交互仅依赖原生 <details>，不引入任何 JS 依赖。
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Template

# 与前端 ToolCard LABELS 保持一致的中文动作名
TOOL_LABELS: dict[str, str] = {
    "list_directory": "查看目录", "read_file": "读取文件", "search_text": "搜索文本",
    "repo_map": "项目结构", "detect_project": "项目识别", "create_directory": "创建目录",
    "edit_files": "修改文件", "clone_repository": "克隆仓库", "run_command": "执行命令",
    "ensure_venv": "准备环境", "install_dependencies": "安装依赖", "verify_project": "验证项目",
    "start_process": "启动进程", "get_process": "查看进程", "list_processes": "进程列表",
    "stop_process": "停止进程", "check_port": "检查端口", "wait_http": "等待服务",
    "http_request": "HTTP 请求", "http_request_batch": "批量请求", "web_fetch": "网页抓取",
    "web_search": "网页搜索", "use_skill": "调用技能", "spawn_subagent": "子代理",
    "spawn_subagents": "并行子代理",
}

STATUS_TEXT = {
    "succeeded": "完成", "failed": "失败", "rejected": "失败",
    "interrupted": "中断", "running": "进行中",
}

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=True)


def render_markdown_subset(text: str) -> str:
    """极简 markdown：代码块 / 标题 / 列表 / 粗体 / 行内代码；其余按段落换行。"""
    if not text:
        return ""
    escaped = _esc(text)
    out: list[str] = []
    in_code = False
    code_buf: list[str] = []
    for raw in escaped.split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                out.append(f'<pre class="code">{chr(10).join(code_buf)}</pre>')
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("### "):
            out.append(f"<h4>{stripped[4:]}</h4>")
        elif stripped.startswith("## "):
            out.append(f"<h3>{stripped[3:]}</h3>")
        elif stripped.startswith("# "):
            out.append(f"<h2>{stripped[2:]}</h2>")
        elif stripped.startswith(("- ", "* ")):
            out.append(f'<div class="li">• {stripped[2:]}</div>')
        elif stripped.startswith(("1. ", "2. ", "3. ", "4. ", "5. ", "6. ", "7. ", "8. ", "9. ")):
            out.append(f'<div class="li">{stripped}</div>')
        elif not stripped:
            out.append('<div class="sp"></div>')
        else:
            out.append(f'<div class="p">{stripped}</div>')
    if in_code and code_buf:
        out.append(f'<pre class="code">{chr(10).join(code_buf)}</pre>')
    body = "\n".join(out)
    body = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", body)
    body = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", body)
    return body


def parse_unified_diff(diff_text: str) -> list[dict]:
    """unified diff → 文件段列表（含 hunk 与逐行渲染数据）。"""
    files: list[dict] = []
    current: dict | None = None
    hunk: dict | None = None
    old_no = new_no = 0
    for raw in (diff_text or "").split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        if line.startswith("--- a/"):
            current = {"path": line[6:], "hunks": []}
            files.append(current)
            hunk = None
            continue
        if not current or line.startswith("+++ b/"):
            continue
        m = _HUNK_RE.match(line)
        if m:
            hunk = {"head": line, "lines": []}
            current["hunks"].append(hunk)
            old_no, new_no = int(m.group(1)), int(m.group(2))
            continue
        if line.startswith("\\") or hunk is None:
            continue
        if not line:
            # 末尾 split 产生的裸空行不是 diff 内容（真正的空上下文行带前导空格）
            continue
        if line.startswith("+"):
            hunk["lines"].append({"kind": "add", "old": "", "new": str(new_no), "text": line[1:]})
            new_no += 1
        elif line.startswith("-"):
            hunk["lines"].append({"kind": "del", "old": str(old_no), "new": "", "text": line[1:]})
            old_no += 1
        else:
            text = line[1:] if line.startswith(" ") else line
            hunk["lines"].append({"kind": "ctx", "old": str(old_no), "new": str(new_no), "text": text})
            old_no += 1
            new_no += 1
    return [f for f in files if f["hunks"]]


def _diff_stats(files: Iterable[dict]) -> tuple[int, int]:
    adds = dels = 0
    for f in files:
        for h in f["hunks"]:
            for ln in h["lines"]:
                if ln["kind"] == "add":
                    adds += 1
                elif ln["kind"] == "del":
                    dels += 1
    return adds, dels


def _step_view(step: dict) -> dict:
    name = str(step.get("toolName") or "")
    label = TOOL_LABELS.get(name, name or "工具调用")
    status = "failed" if (step.get("error") or step.get("status") in ("failed", "rejected")) else (
        "interrupted" if step.get("status") == "interrupted" else
        "running" if not step.get("done") else "succeeded")
    output = str(step.get("output") or "")
    if len(output) > 4000:
        output = output[:4000] + "\n…（已截断）"
    error = str(step.get("error") or "")
    args = step.get("args") or {}
    data = step.get("data") or {}
    diff_files = parse_unified_diff(str(data.get("diff") or ""))
    if not diff_files:
        diff_files = _diff_from_args(args)
    adds, dels = _diff_stats(diff_files)
    return {
        "label": label, "name": name, "status": status,
        "status_text": STATUS_TEXT.get(status, "完成"),
        "phrase": _arg_phrase(name, args),
        "args_json": json_dumps(args) if args else "",
        "output": output,
        "error": error,
        "diff_files": diff_files,
        "diff_add": adds, "diff_del": dels,
    }


def _diff_from_args(args: dict) -> list[dict]:
    """结果缺失时用 args.edits 兜底构建（写=全绿；替换=红块+绿块）。"""
    edits = args.get("edits") if isinstance(args, dict) else None
    if not isinstance(edits, list):
        return []
    by_path: dict[str, list[dict]] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        path = str(edit.get("path") or "").strip()
        if not path:
            continue
        lines: list[dict] = []
        if edit.get("operation") == "replace" and isinstance(edit.get("search"), str):
            for i, l in enumerate(str(edit["search"]).split("\n"), 1):
                lines.append({"kind": "del", "old": str(i), "new": "", "text": l})
        if isinstance(edit.get("content"), str):
            start = len(lines) + 1
            for i, l in enumerate(str(edit["content"]).split("\n"), 1):
                lines.append({"kind": "add", "old": "", "new": str(i), "text": l})
        if lines:
            by_path.setdefault(path, []).append({"head": "", "lines": lines})
    return [{"path": p, "hunks": h} for p, h in by_path.items()]


def _arg_phrase(name: str, args: dict) -> str:
    def pick(key: str) -> str:
        v = args.get(key) if isinstance(args, dict) else None
        return v.strip() if isinstance(v, str) else ""

    if name == "read_file":
        p = pick("path")
        return f"读取 {p}" if p else ""
    if name in ("list_directory", "create_directory"):
        p = pick("path")
        return p or ""
    if name == "search_text":
        q = pick("query")
        return f'搜索 "{q}"' if q else ""
    if name == "edit_files":
        edits = args.get("edits") if isinstance(args, dict) else None
        paths = sorted({str(e.get("path")) for e in edits if isinstance(e, dict) and e.get("path")}) if isinstance(edits, list) else []
        return f"修改 {len(paths)} 个文件（{paths[0]}）" if paths else ""
    if name == "run_command":
        c = pick("command")
        return c[:70] if c else ""
    if name in ("web_fetch", "http_request"):
        return pick("url")
    if name == "web_search":
        q = pick("query")
        return f'"{q}"' if q else ""
    first = next((v for v in (args or {}).values() if isinstance(v, str) and v.strip()), "")
    return first.strip()[:60]


def json_dumps(value: Any) -> str:
    import json
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = str(value)
    return text[:2000] + "\n…（已截断）" if len(text) > 2000 else text


def _fmt_elapsed(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds} 秒"
    m, s = divmod(seconds, 60)
    return f"{m} 分 {s} 秒" if s else f"{m} 分钟"


def _entries_for(message: dict) -> list[dict]:
    """按 timeline 还原交错顺序；无 timeline 时按 思考→工具→命令→旁白 兜底。"""
    steps = [s for s in (message.get("steps") or []) if isinstance(s, dict)]
    thinking = [t for t in (message.get("thinking") or []) if isinstance(t, dict)]
    narrations = [str(n) for n in (message.get("narrations") or []) if str(n).strip()]
    cmd_blocks = [c for c in (message.get("cmdBlocks") or []) if isinstance(c, dict)]
    timeline = [t for t in (message.get("timeline") or []) if isinstance(t, dict)]

    step_by_call = {s.get("callId"): s for s in steps if s.get("callId")}
    cmd_by_id = {c.get("id"): c for c in cmd_blocks if c.get("id")}
    think_cursor: dict[int, int] = {}
    used_steps: set[int] = set()
    entries: list[dict] = []

    def think_entry(round_: int) -> dict | None:
        segs = [t for t in thinking if int(t.get("round", 0)) == round_]
        idx = think_cursor.get(round_, 0)
        if idx >= len(segs):
            return None
        think_cursor[round_] = idx + 1
        return {"kind": "think", "text": str(segs[idx].get("content") or "")}

    if timeline:
        for item in timeline:
            kind = item.get("kind")
            if kind == "think":
                e = think_entry(int(item.get("round", 0)))
                if e:
                    entries.append(e)
            elif kind == "tool":
                step = step_by_call.get(item.get("callId"))
                if step is not None:
                    used_steps.add(id(step))
                    entries.append({"kind": "tool", "step": _step_view(step)})
            elif kind == "cmd":
                block = cmd_by_id.get(item.get("id"))
                if block:
                    entries.append({"kind": "cmd", "block": _cmd_view(block)})
            elif kind == "note":
                text = str(item.get("text") or "").strip()
                if text:
                    entries.append({"kind": "note", "text": text})
    leftovers = [s for s in steps if id(s) not in used_steps]
    if leftovers and not timeline:
        for t in thinking:
            entries.append({"kind": "think", "text": str(t.get("content") or "")})
        for s in leftovers:
            entries.append({"kind": "tool", "step": _step_view(s)})
        for c in cmd_blocks:
            entries.append({"kind": "cmd", "block": _cmd_view(c)})
        entries.extend({"kind": "note", "text": n} for n in narrations)
    return entries


def _cmd_view(block: dict) -> dict:
    lines = "\n".join(str(l) for l in (block.get("lines") or []))
    if len(lines) > 4000:
        lines = lines[:4000] + "\n…（已截断）"
    return {
        "command": str(block.get("command") or ""),
        "risk": "high" if block.get("risk") == "high" else "safe",
        "done": bool(block.get("done")),
        "lines": lines,
    }


def build_view(messages: list[dict], title: str) -> dict:
    """chat_messages → 模板视图数据。"""
    tool_total = 0
    work_total = 0
    rendered: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "user":
            rendered.append({"kind": "user", "body": render_markdown_subset(str(msg.get("content") or ""))})
            continue
        steps = msg.get("steps") or []
        entries = _entries_for(msg)
        tools = sum(1 for e in entries if e["kind"] == "tool")
        thinks = sum(1 for e in entries if e["kind"] == "think")
        elapsed = int(msg.get("workElapsed") or 0)
        tool_total += tools
        work_total += elapsed
        agent_run = msg.get("agentRun") or {}
        rendered.append({
            "kind": "assistant",
            "body": render_markdown_subset(str(msg.get("content") or "")),
            "has_work": bool(entries),
            "work_elapsed": _fmt_elapsed(elapsed),
            "tool_count": tools,
            "think_count": thinks,
            "status": STATUS_TEXT.get(str(agent_run.get("status") or ""), ""),
            "entries": entries,
            "fallback_steps": [s for s in steps if not msg.get("timeline")][:0],
        })
    return {
        "title": title or "会话分享",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "messages": rendered,
        "message_count": len(rendered),
        "tool_total": tool_total,
        "work_total": _fmt_elapsed(work_total),
    }


_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} · GitHub Explorer 会话分享</title>
<style>
:root{
  --bg:#0d1117; --surface:#161b22; --surface-2:#1c2128; --border:#21262d; --border-2:#30363d;
  --text:#c9d1d9; --text-2:#e6edf3; --muted:#8b949e; --accent:#58a6ff;
  --green:#3fb950; --green-bg:rgba(63,185,80,.15); --red:#f85149; --red-bg:rgba(248,81,73,.12);
  --mono:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--accent)}
.wrap{max-width:860px;margin:0 auto;padding:0 20px 80px}
.hero{padding:56px 0 28px;border-bottom:1px solid var(--border);margin-bottom:28px}
.brand{font:600 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);display:flex;align-items:center;gap:8px}
.brand i{width:8px;height:8px;border-radius:50%;background:var(--green);display:inline-block}
h1{font-size:26px;line-height:1.35;margin:14px 0 10px;color:var(--text-2);font-weight:650}
.meta{color:var(--muted);font-size:12.5px}
.meta b{color:var(--text);font-weight:600}
.msg{margin:26px 0}
.role{display:flex;align-items:center;gap:8px;font:600 11px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.role .dot{width:18px;height:18px;border-radius:5px;display:inline-flex;align-items:center;justify-content:center;font-size:9px;color:#fff}
.msg--user .dot{background:linear-gradient(135deg,#58a6ff,#7c3aed)}
.msg--assistant .dot{background:linear-gradient(135deg,#2ea043,#1a7f6e)}
.msg--user .body{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px}
.msg--assistant .body{margin-top:14px}
.body .p{margin:8px 0}
.body .sp{height:6px}
.body h2,.body h3,.body h4{color:var(--text-2);margin:18px 0 8px;line-height:1.4}
.body h2{font-size:18px}.body h3{font-size:16px}.body h4{font-size:14px}
.body .li{margin:4px 0;padding-left:6px}
.body code{background:var(--surface-2);border:1px solid var(--border);border-radius:4px;padding:1px 5px;font:12px var(--mono)}
.body pre.code{background:#0b0d10;border:1px solid var(--border);border-radius:8px;padding:12px 14px;overflow:auto;font:12px/1.6 var(--mono);color:#a3adba}
.work{border:1px solid var(--border-2);border-radius:10px;background:var(--surface);overflow:hidden}
.work>summary{cursor:pointer;list-style:none;padding:11px 14px;display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--text-2);user-select:none}
.work>summary::-webkit-details-marker{display:none}
.work>summary:hover{background:var(--surface-2)}
.work .chip{padding:2px 8px;border-radius:9px;background:rgba(110,118,129,.15);color:var(--muted);font:600 10px/16px var(--mono)}
.work .chev{margin-left:auto;color:var(--muted);transition:transform .15s}
.work[open] .chev{transform:rotate(180deg)}
.wbody{padding:10px 12px;display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--border);background:#0f1319}
.card{border:1px solid var(--border);border-radius:8px;background:var(--surface);overflow:hidden}
.card>summary{cursor:pointer;list-style:none;padding:8px 12px;display:flex;align-items:center;gap:8px;font-size:12px;user-select:none}
.card>summary::-webkit-details-marker{display:none}
.card>summary:hover{background:var(--surface-2)}
.card .name{font-weight:600;color:var(--text-2);flex-shrink:0}
.card .sub{color:var(--muted);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.card .stat{color:var(--muted);font:10.5px var(--mono);flex-shrink:0}
.card .stat.add{color:var(--green)}
.card .stat.del{color:var(--red)}
.card .badge{font:10px var(--mono);color:var(--muted);flex-shrink:0}
.card .badge.ok{color:var(--green)}.card .badge.bad{color:var(--red)}
.card .cbody{padding:9px 12px;border-top:1px solid var(--border);display:grid;gap:8px;background:var(--surface-2);font-size:12px}
.card .cbody pre{margin:0;padding:8px 10px;background:#0b0d10;border:1px solid var(--border);border-radius:6px;overflow:auto;max-height:320px;font:11px/1.6 var(--mono);color:#a3adba;white-space:pre-wrap;word-break:break-word}
.think .cbody{color:var(--muted);white-space:pre-wrap;word-break:break-word;max-height:300px;overflow:auto;font-size:12px}
.note{padding:6px 10px;color:var(--muted);font-size:12px;border-left:2px solid var(--border-2)}
.diff{border:1px solid var(--border);border-radius:8px;overflow:hidden;background:#0b0d10}
.diff .fhead{padding:6px 10px;background:var(--surface-2);border-bottom:1px solid var(--border);font:600 11px var(--mono);color:var(--text-2);display:flex;align-items:center;gap:8px}
.diff .fstat{margin-left:auto;font-size:10.5px}
.diff .fstat .a{color:var(--green)}.diff .fstat .d{color:var(--red)}
.diff .hhead{padding:3px 10px;color:var(--muted);background:#11151c;border-bottom:1px solid var(--border);font:10px/16px var(--mono)}
.diff .lines{max-height:340px;overflow:auto;font:11px/1.65 var(--mono)}
.diff .ln{display:flex;min-width:max-content}
.diff .ln .no{width:34px;flex-shrink:0;text-align:right;padding-right:8px;color:var(--muted);opacity:.55;user-select:none}
.diff .ln .mk{width:14px;flex-shrink:0;text-align:center;color:var(--muted)}
.diff .ln .tx{white-space:pre;padding-right:14px;color:#a3adba}
.diff .ln.add{background:var(--green-bg)}.diff .ln.add .mk{color:var(--green);font-weight:700}
.diff .ln.del{background:var(--red-bg)}.diff .ln.del .mk{color:var(--red);font-weight:700}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--border);color:var(--muted);font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
@media(max-width:560px){h1{font-size:20px}.wrap{padding:0 14px 60px}}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <div class="brand"><i></i> GitHub Explorer · 会话分享</div>
    <h1>{{ title }}</h1>
    <div class="meta">
      <b>{{ message_count }}</b> 条消息 · 工具调用 <b>{{ tool_total }}</b> 次{% if work_total %} · 累计工作 <b>{{ work_total }}</b>{% endif %} · 生成于 {{ generated_at }}
    </div>
  </header>
  {% for m in messages %}
    {% if m.kind == 'user' %}
    <section class="msg msg--user">
      <div class="role"><span class="dot">你</span> 用户</div>
      <div class="body">{{ m.body|safe }}</div>
    </section>
    {% else %}
    <section class="msg msg--assistant">
      <div class="role"><span class="dot">E</span> Explorer{% if m.status %} · {{ m.status }}{% endif %}</div>
      {% if m.has_work %}
      <details class="work"{% if not m.body %} open{% endif %}>
        <summary>
          已工作 {{ m.work_elapsed or '—' }}
          {% if m.tool_count %}<span class="chip">{{ m.tool_count }} 个操作</span>{% endif %}
          {% if m.think_count %}<span class="chip">{{ m.think_count }} 段思考</span>{% endif %}
          <span class="chev">▾</span>
        </summary>
        <div class="wbody">
          {% for e in m.entries %}
            {% if e.kind == 'think' %}
            <details class="card think"><summary><span class="name">思考</span><span class="sub">{{ e.text[:60] }}</span></summary><div class="cbody">{{ e.text }}</div></details>
            {% elif e.kind == 'note' %}
            <div class="note">{{ e.text }}</div>
            {% elif e.kind == 'cmd' %}
            <details class="card"><summary><span class="name">执行命令</span><span class="sub">{{ e.block.command[:70] }}</span>{% if e.block.risk == 'high' %}<span class="badge bad">高风险</span>{% endif %}</summary><div class="cbody">{% if e.block.lines %}<pre>{{ e.block.lines }}</pre>{% endif %}</div></details>
            {% elif e.kind == 'tool' %}
            <details class="card"><summary>
              <span class="name">{{ e.step.label }}</span>
              <span class="sub">{{ e.step.phrase }}</span>
              {% if e.step.diff_add or e.step.diff_del %}<span class="stat add">+{{ e.step.diff_add }}</span><span class="stat del">−{{ e.step.diff_del }}</span>{% endif %}
              <span class="badge {% if e.step.status == 'succeeded' %}ok{% elif e.step.status in ('failed','rejected') %}bad{% endif %}">{{ e.step.status_text }}</span>
            </summary><div class="cbody">
              {% if e.step.error %}<pre>{{ e.step.error }}</pre>{% endif %}
              {% for f in e.step.diff_files %}
              <div class="diff">
                <div class="fhead">📄 {{ f.path }}</div>
                {% for h in f.hunks %}
                  {% if h.head %}<div class="hhead">{{ h.head }}</div>{% endif %}
                  <div class="lines">
                  {% for ln in h.lines %}
                    <div class="ln {{ ln.kind }}"><span class="no">{{ ln.old }}</span><span class="no">{{ ln.new }}</span><span class="mk">{% if ln.kind == 'add' %}+{% elif ln.kind == 'del' %}−{% else %} {% endif %}</span><span class="tx">{{ ln.text or ' ' }}</span></div>
                  {% endfor %}
                  </div>
                {% endfor %}
              </div>
              {% endfor %}
              {% if e.step.output and not e.step.diff_files %}<pre>{{ e.step.output }}</pre>{% endif %}
              {% if e.step.args_json and not e.step.diff_files %}<pre>{{ e.step.args_json }}</pre>{% endif %}
            </div></details>
            {% endif %}
          {% endfor %}
        </div>
      </details>
      {% endif %}
      {% if m.body %}<div class="body">{{ m.body|safe }}</div>{% endif %}
    </section>
    {% endif %}
  {% endfor %}
  <footer>
    <span>由 GitHub Explorer 生成 · 会话内容可能包含本地路径与代码片段</span>
    <span>{{ generated_at }}</span>
  </footer>
</div>
</body>
</html>
""", autoescape=True)


def render_share_html(messages: list[dict], title: str) -> str:
    # autoescape：模板内所有变量自动 HTML 转义（markdown 片段以 |safe 输出，其内部已转义）
    return _TEMPLATE.render(**build_view(messages, title))


def write_share(messages: list[dict], title: str, share_dir: Path, token: str) -> Path:
    share_dir.mkdir(parents=True, exist_ok=True)
    target = share_dir / f"{token}.html"
    target.write_text(render_share_html(messages, title), encoding="utf-8")
    return target
