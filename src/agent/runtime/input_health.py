"""Small, deterministic checks for request text corruption on Windows."""

from __future__ import annotations

import locale
import sys


def inspect_text_encoding(text: str) -> dict[str, str | bool]:
    value = str(text or "")
    if "\ufffd" in value:
        return {
            "status": "corrupted",
            "reason": "replacement_character",
            "message": "请求文本包含 Unicode 替换字符（�），可能在传输过程中损坏。请重新发送。",
        }
    compact = value.replace(" ", "")
    if "????" in compact and not any("\u3400" <= char <= "\u9fff" for char in value):
        return {
            "status": "corrupted",
            "reason": "question_mark_substitution",
            "message": "请求文本疑似被 Windows 编码转换成连续问号（????），请检查终端/浏览器编码后重试。",
        }
    return {"status": "intact", "reason": "ok", "message": ""}


def encoding_health() -> dict[str, object]:
    probe = "编码健康检查"
    round_trip = probe.encode("utf-8").decode("utf-8") == probe
    return {
        "ok": round_trip,
        "encoding": "utf-8",
        "probe": probe,
        "round_trip": round_trip,
        "python_encoding": sys.getdefaultencoding(),
        "locale_encoding": locale.getpreferredencoding(False),
    }
