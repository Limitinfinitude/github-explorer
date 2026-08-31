"""
命令执行工具 — run_command, run_command_stream, classify_command_risk
"""
import os
import subprocess
import platform
import asyncio
import re as _re_cmd


_DANGEROUS_PATTERNS = [
    (r"rm\s+-[rf]+\s+/", "递归删除根目录"),
    (r"del\s+/[sfaq].*\*", "批量删除系统文件"),
    (r"format\s+[a-zA-Z]:", "格式化磁盘"),
    (r"mkfs\.", "格式化分区"),
    (r"DROP\s+(?:TABLE|DATABASE)\s+", "删除数据库对象"),
    (r"TRUNCATE\s+TABLE\s+", "清空数据库表"),
    (r"rmdir\s+/[sq]", "强制删除目录"),
    (r":\(\)\{.*\}", "Fork 炸弹"),
    (r"curl\s+.+\|\s*(?:ba)?sh", "管道执行远程脚本"),
    (r"wget\s+.+\|\s*(?:ba)?sh", "管道执行远程脚本"),
]


def classify_command_risk(cmd: str) -> dict:
    """检测命令风险等级。返回 {'risk': 'safe'|'high', 'reason': str}

    先匹配轻量模式特有的高危模式（Fork 炸弹/管道执行远程脚本等），
    再叠加 harness 的 ToolRisk 分级——DESTRUCTIVE/PRIVILEGED/EXTERNAL/BOUNDARY
    一律标 high，保证与主循环同一条风险口径。
    """
    for pattern, reason in _DANGEROUS_PATTERNS:
        if _re_cmd.search(pattern, cmd, _re_cmd.IGNORECASE):
            return {"risk": "high", "reason": reason}
    from agent.runtime.tooling import classify_command_risk as _harness_classify
    from agent.runtime.models import ToolRisk
    risk = _harness_classify({"command": cmd})
    if risk in (ToolRisk.DESTRUCTIVE, ToolRisk.PRIVILEGED, ToolRisk.EXTERNAL, ToolRisk.BOUNDARY):
        return {"risk": "high", "reason": f"harness 风险分级: {risk.value}"}
    return {"risk": "safe", "reason": ""}


def run_command(cmd: str, cwd: str = None, timeout: int = 60) -> dict:
    """
    执行 shell 命令。

    Windows 下使用 PowerShell，Linux/Mac 下使用 bash。
    执行体复用 harness 的 plan_shell_command（UTF-8 编码修复 + bash/curl 检测），
    保证轻量模式与主循环同一条命令编码与打包链路（之前是另一套裸 powershell，中文乱码）。
    """
    try:
        from agent.runtime.commands import plan_shell_command

        # 清理环境变量，避免 conda 等环境注入噪音
        clean_env = {k: v for k, v in os.environ.items()
                     if not k.startswith("CONDA") and k != "SSL_CERT_FILE"}

        plan = plan_shell_command(cmd)
        if plan.error:
            return {"success": False, "output": f"{plan.error} {plan.suggestion or ''}".strip()}

        result = subprocess.run(
            plan.args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=clean_env,
        )

        output = result.stdout
        if result.stderr:
            # 过滤掉 conda/anaconda 环境噪音
            stderr_lines = [
                line for line in result.stderr.splitlines()
                if "SSL_CERT_FILE" not in line
                and "conda" not in line.lower()
                and "Remove-Item" not in line
                and "openssl_deactivate" not in line
            ]
            if stderr_lines:
                output += "\n" + "\n".join(stderr_lines)

        return {"success": result.returncode == 0, "output": output.strip()}

    except subprocess.TimeoutExpired:
        return {"success": False, "output": "命令执行超时"}
    except Exception as e:
        return {"success": False, "output": f"执行错误: {str(e)}"}


async def run_command_stream(cmd: str, cwd: str = None, timeout: int = 60):
    """
    异步流式执行命令（Open Interpreter 风格）。
    Yields:
      {"type": "line", "text": str}
      {"type": "done", "success": bool, "returncode": int}
      {"type": "error", "text": str}
    """
    from agent.runtime.commands import plan_shell_command

    clean_env = {k: v for k, v in os.environ.items()
                 if not k.startswith("CONDA") and k != "SSL_CERT_FILE"}

    plan = plan_shell_command(cmd)
    if plan.error:
        yield {"type": "error", "text": f"{plan.error} {plan.suggestion or ''}".strip()}
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            *plan.args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=clean_env,
        )

        queue: asyncio.Queue = asyncio.Queue()

        async def _drain(stream, label: str):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if label == "err" and any(x in text for x in (
                        "SSL_CERT_FILE", "openssl_deactivate", "Remove-Item", "conda")):
                    continue
                await queue.put(("line", text))
            await queue.put(("done_pipe", label))

        tasks = [
            asyncio.create_task(_drain(proc.stdout, "out")),
            asyncio.create_task(_drain(proc.stderr, "err")),
        ]

        finished_pipes = 0
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while finished_pipes < 2:
            remaining = deadline - loop.time()
            if remaining <= 0:
                proc.kill()
                yield {"type": "error", "text": "命令执行超时"}
                await asyncio.gather(*tasks, return_exceptions=True)
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                continue
            kind, val = item
            if kind == "line":
                yield {"type": "line", "text": val}
            elif kind == "done_pipe":
                finished_pipes += 1

        await asyncio.gather(*tasks, return_exceptions=True)
        returncode = await proc.wait()
        yield {"type": "done", "success": returncode == 0, "returncode": returncode}

    except Exception as e:
        yield {"type": "error", "text": str(e)}
