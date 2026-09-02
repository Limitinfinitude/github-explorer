"""Histogram 行级 diff 引擎（低频锚定，面向代码可读性）。

替代 difflib.SequenceMatcher（Ratcliff–Obershelp，最坏 O(n²)，锚点选择随意）：
- 每个待定区段先在目标段构建"行 → 出现次数"直方图；
- 优先锚定在目标段中出现次数最少的公共行（出现 1 次即最优，立即停止）；
- 以锚点递归切分左右子段，锚点之间整段判定为 增/删/替换；
- 输出与 difflib.unified_diff 同构的文本（---/+++/@@ 头、+/-/ 前缀、3 行上下文），
  前端 diffView 解析器无需改动。
"""

from typing import Optional, Sequence

Op = tuple[str, int, int, int, int]


def histogram_opcodes(a: Sequence[str], b: Sequence[str]) -> list[Op]:
    """a/b 为按行切分（保留换行符）的文本行；返回 difflib.SequenceMatcher 同构的 opcodes：
    (tag, i1, i2, j1, j2)，tag ∈ equal/delete/insert/replace。"""
    ops: list[Op] = []
    stack = [(0, len(a), 0, len(b))]
    while stack:
        a_s, a_e, b_s, b_e = stack.pop()
        # 公共前缀/后缀直接裁掉（也是高频重复行的主要出口）；裁剪掉的段记为 equal
        pre_s, pre_b = a_s, b_s
        while a_s < a_e and b_s < b_e and a[a_s] == b[b_s]:
            a_s += 1
            b_s += 1
        if a_s > pre_s:
            ops.append(('equal', pre_s, a_s, pre_b, b_s))
        suf_s, suf_b = a_e, b_e
        while a_s < a_e and b_s < b_e and a[a_e - 1] == b[b_e - 1]:
            a_e -= 1
            b_e -= 1
        if a_e < suf_s:
            ops.append(('equal', a_e, suf_s, b_e, suf_b))
        if a_s == a_e and b_s == b_e:
            continue
        if a_s == a_e or b_s == b_e:
            ops.append(('delete' if a_s < a_e else 'insert', a_s, a_e, b_s, b_e))
            continue
        anchor = _find_anchor(a, b, a_s, a_e, b_s, b_e)
        if anchor is None:
            # 没有公共行：整段替换
            ops.append(('replace', a_s, a_e, b_s, b_e))
            continue
        ai, bi = anchor
        ops.append(('equal', ai, ai + 1, bi, bi + 1))
        stack.append((ai + 1, a_e, bi + 1, b_e))
        stack.append((a_s, ai, b_s, bi))
    ops.sort(key=lambda op: (op[1], op[3]))
    return ops


def _find_anchor(
    a: Sequence[str], b: Sequence[str], a_s: int, a_e: int, b_s: int, b_e: int
) -> Optional[tuple[int, int]]:
    """在 [a_s,a_e) × [b_s,b_e) 内找锚点：b 段中出现次数最少的公共行（唯一行即最优）。"""
    hist: dict[str, int] = {}
    for line in b[b_s:b_e]:
        hist[line] = hist.get(line, 0) + 1
    first: dict[str, int] = {}
    for idx in range(b_s, b_e):
        line = b[idx]
        if line not in first:
            first[line] = idx
    best: Optional[tuple[int, int, int]] = None  # (count, a_idx, b_idx)
    for i in range(a_s, a_e):
        cnt = hist.get(a[i])
        if cnt is None:
            continue
        if best is None or cnt < best[0]:
            best = (cnt, i, first[a[i]])
            if cnt == 1:
                break
    return None if best is None else (best[1], best[2])


def format_unified_diff(
    a: Sequence[str],
    b: Sequence[str],
    fromfile: str = "a",
    tofile: str = "b",
    context: int = 3,
) -> list[str]:
    """opcodes → unified diff 文本行（---/+++ 头 + 分组 hunk，与 difflib.unified_diff 同构）。"""
    out = [f"--- {fromfile}\n", f"+++ {tofile}\n"]
    events: list[tuple[str, Optional[int], Optional[int], str]] = []
    old_no = 1
    new_no = 1
    for tag, i1, i2, j1, j2 in histogram_opcodes(a, b):
        if tag == 'equal':
            for k in range(i1, i2):
                events.append(('ctx', old_no, new_no, a[k]))
                old_no += 1
                new_no += 1
        elif tag == 'delete':
            for k in range(i1, i2):
                events.append(('del', old_no, None, a[k]))
                old_no += 1
        elif tag == 'insert':
            for k in range(j1, j2):
                events.append(('ins', None, new_no, b[k]))
                new_no += 1
        else:  # replace = 先删后增
            for k in range(i1, i2):
                events.append(('del', old_no, None, a[k]))
                old_no += 1
            for k in range(j1, j2):
                events.append(('ins', None, new_no, b[k]))
                new_no += 1
    change_idx = [i for i, e in enumerate(events) if e[0] != 'ctx']
    if not change_idx:
        return out
    # 相邻变更区间隔 ≤ 2*context 时并入同一 hunk
    hunks: list[list[tuple[str, Optional[int], Optional[int], str]]] = []
    i = 0
    n = len(change_idx)
    while i < n:
        first = change_idx[i]
        last = first
        while i + 1 < n and change_idx[i + 1] - last - 1 <= 2 * context:
            last = change_idx[i + 1]
            i += 1
        i += 1
        hunks.append(events[max(0, first - context):min(len(events), last + context + 1)])
    for hunk in hunks:
        old_s = next((e[1] for e in hunk if e[1] is not None), 0)
        new_s = next((e[2] for e in hunk if e[2] is not None), 0)
        old_cnt = sum(1 for e in hunk if e[1] is not None)
        new_cnt = sum(1 for e in hunk if e[2] is not None)
        out.append(f"@@ -{_fmt_range(old_s, old_cnt)} +{_fmt_range(new_s, new_cnt)} @@\n")
        for tag, _old, _new, line in hunk:
            prefix = ' ' if tag == 'ctx' else ('-' if tag == 'del' else '+')
            out.append(prefix + line)
    return out


def _fmt_range(start: int, count: int) -> str:
    """difflib 风格：count==1 省略 ",1"；count==0 保留 ",0"（纯增/删 hunk 显示 -0,0 / +0,0）。"""
    return str(start) if count == 1 else f"{start},{count}"
