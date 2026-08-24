"""技能系统（Skills）：按需加载的提示词包。

对齐 Claude Code / pi 的 agentskills.io 规范（目录 + SKILL.md + frontmatter）
与 Codex「技能 = 按需加载的提示词」：
- 描述索引（name + description 一行）常驻系统提示词，模型据此决定是否调用；
- 正文经 use_skill 工具按需注入消息，不塞进系统提示词（省 token，可扩展）。

技能来源（按优先级合并）：
1. 内置技能：<repo>/src/agent/skills/<name>/SKILL.md（随 harness 部署）；
2. 项目技能：<workspace>/.github-explorer/skills/<name>/SKILL.md（随项目）。
"""
import functools
import re
from dataclasses import dataclass, field
from pathlib import Path

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
MAX_BODY_BYTES = 8_192  # 单技能正文上限，防目录内塞超大文件


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source: str

    @property
    def index_line(self) -> str:
        return f"- {self.name}: {self.description}"


def _parse_skill(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    frontmatter: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            frontmatter[key] = value
    name = frontmatter.get("name", "").strip()
    description = frontmatter.get("description", "").strip()
    if not name or not description:
        return None
    body = text[match.end():].strip()
    if not body:
        return None
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        body = body[:MAX_BODY_BYTES] + "\n…（技能正文超长已截断）"
    return Skill(name=name, description=description, body=body, source=str(path))


def _discover(extra_dirs: list[Path] | None = None) -> dict[str, Skill]:
    """扫描技能目录，name 冲突时内置优先（先内置后项目，后写覆盖）。"""
    found: dict[str, Skill] = {}
    roots: list[Path] = [BUILTIN_SKILLS_DIR, *(extra_dirs or [])]
    for root in roots:
        if not root.is_dir():
            continue
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill = _parse_skill(skill_dir / "SKILL.md")
            if skill is not None:
                found[skill.name] = skill
    return found


@functools.lru_cache(maxsize=32)
def _cached_discover(root_key: str) -> dict[str, Skill]:
    extra = [Path(p) for p in root_key.split("|") if p]
    return _discover(extra)


def load_skills(workspace_root: Path | str | None = None) -> dict[str, Skill]:
    """返回 {name: Skill}。workspace_root 提供时并入项目级技能目录。"""
    extra = []
    if workspace_root:
        project_skills = Path(workspace_root).expanduser().resolve() / ".github-explorer" / "skills"
        if project_skills.is_dir():
            extra.append(project_skills)
    return _cached_discover("|".join(str(p) for p in extra))


def skill_index(workspace_root: Path | str | None = None) -> str:
    """系统提示词用的技能描述索引（每行一个，空则返回空串）。"""
    skills = load_skills(workspace_root)
    if not skills:
        return ""
    lines = sorted(skill.index_line for skill in skills.values())
    return "\n".join(lines)
