"""diff_engine（Histogram 行级 diff）单元测试：
- roundtrip：任意输入下，diff 应用到 before 必须精确重建 after（正确性）；
- 格式：hunk 头与 difflib 同构，纯增/删文件的 -0,0 边界；
- 可读性：低频唯一行被保留为锚点，高频重复行不产生碎片化 equal。
"""

import random
import re

from src.agent.runtime.diff_engine import format_unified_diff, histogram_opcodes

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_diff(diff_text: str, before: list[str]) -> list[str]:
    """把 unified diff 文本应用到 before 行列表，返回重建的 after（测试辅助）。"""
    after: list[str] = []
    old_pos = 1  # before 中的下一行号（1-based）
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("---") or line.startswith("+++"):
            continue
        m = HUNK_RE.match(line)
        if m:
            old_start = int(m.group(1))
            # 跳到 hunk 起点：hunk 之间未变更的行直接透传
            while old_pos < old_start and old_pos <= len(before):
                after.append(before[old_pos - 1])
                old_pos += 1
            continue
        if line.startswith("\\"):
            continue
        if line.startswith(" "):
            assert old_pos <= len(before) and before[old_pos - 1] == line[1:], \
                f"上下文行不匹配: {before[old_pos-1]!r} vs {line[1:]!r}"
            after.append(before[old_pos - 1])
            old_pos += 1
        elif line.startswith("-"):
            assert old_pos <= len(before) and before[old_pos - 1] == line[1:]
            old_pos += 1
        elif line.startswith("+"):
            after.append(line[1:])
    while old_pos <= len(before):
        after.append(before[old_pos - 1])
        old_pos += 1
    return after


def diff_of(before: str, after: str) -> str:
    return "".join(format_unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="a/f", tofile="b/f",
    ))


def test_roundtrip_simple_cases():
    cases = [
        ("a\nb\nc\n", "a\nx\nc\n"),
        ("a\nb\nc\n", "a\nb\nc\nd\n"),
        ("a\nb\nc\n", ""),
        ("", "a\nb\nc\n"),
        ("a\nb\nc\n", "a\nb\nc\n"),
        ("answer = 41\n", "answer = 42\n"),
        ("line1\n\nline3\n", "line1\n\nnew\nline3\n"),
    ]
    for before, after in cases:
        assert apply_diff(diff_of(before, after), before.splitlines(keepends=True)) \
            == after.splitlines(keepends=True), f"roundtrip failed: {before!r} -> {after!r}"


def test_roundtrip_random():
    rng = random.Random(42)
    pool = ["def f():\n", "    return x\n", "x = 1\n", "\n", "import os\n", "print('hi')\n", "TODO\n"]
    for _ in range(40):
        n_before = rng.randint(0, 40)
        n_after = rng.randint(0, 40)
        before = [rng.choice(pool) for _ in range(n_before)]
        after = [rng.choice(pool) for _ in range(n_after)]
        diff = format_unified_diff(before, after, fromfile="a/f", tofile="b/f")
        assert apply_diff("".join(diff), before) == after, "random roundtrip failed"


def test_hunk_headers_format():
    diff = diff_of("a\nb\nc\nd\ne\nf\ng\n", "a\nb\nX\nd\ne\nf\nY\n")
    headers = [line for line in diff.splitlines() if line.startswith("@@")]
    assert headers, "diff 应包含 hunk"
    for h in headers:
        assert HUNK_RE.match(h), f"hunk 头格式非法: {h}"


def test_new_file_hunk_range():
    diff = diff_of("", "a\nb\n")
    assert "-0,0" in diff and "+1,2" in diff, diff


def test_deleted_all_hunk_range():
    diff = diff_of("a\nb\nc\n", "")
    assert "+0,0" in diff and "-1,3" in diff, diff


def test_single_line_replacement_range():
    diff = diff_of("answer = 41\n", "answer = 42\n")
    assert "@@ -1 +1 @@" in diff, diff


def test_unique_lines_are_anchors():
    """可读性：低频唯一行应保留为 equal 锚点，而不是被卷进替换块。"""
    before_lines = "header\ncommon\ncommon\ncommon\nunique_old\ncommon\ncommon\ncommon\nfooter\n".splitlines(keepends=True)
    after_lines = "header\ncommon\ncommon\ncommon\nunique_new\ncommon\ncommon\ncommon\nfooter\n".splitlines(keepends=True)
    ops = histogram_opcodes(before_lines, after_lines)
    equal_lines = [before_lines[i1:i2] for tag, i1, i2, _j1, _j2 in ops if tag == "equal"]
    flat = [line for block in equal_lines for line in block]
    joined = "".join(flat)
    assert "header" in joined and "footer" in joined
    assert "unique_old" not in joined and "unique_new" not in joined


def test_no_common_lines_is_replace():
    ops = histogram_opcodes(["aaa\n", "bbb\n"], ["xxx\n", "yyy\n"])
    assert ops == [("replace", 0, 2, 0, 2)]


def test_prefix_suffix_trim():
    """公共前缀/后缀整段保留为 equal，不做锚定。"""
    ops = histogram_opcodes(["a\n", "b\n", "c\n", "d\n"], ["a\n", "x\n", "c\n", "d\n"])
    assert ops[0] == ("equal", 0, 1, 0, 1)
    assert ops[-1] == ("equal", 2, 4, 2, 4)


def test_diff_contains_expected_markers():
    """edits.py 现有断言依赖：'-answer = 41' / '+answer = 42' 子串。"""
    diff = diff_of("answer = 41\n", "answer = 42\n")
    assert "-answer = 41" in diff
    assert "+answer = 42" in diff
