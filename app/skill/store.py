"""ProjectOS 的可审查 Skill 目录与加载器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class SkillDocument:
    ref: str
    title: str
    content: str
    source: str

    def as_prompt(self) -> str:
        return f"### Skill {self.ref}: {self.title}\n{self.content.strip()}"


class SkillStore:
    """解析仓库内置 Skill，并允许项目通过 .projectos/skills 覆盖。"""

    def __init__(self, project_path: str | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self._builtin = root / "skills"
        self._project = Path(project_path).resolve() / ".projectos" / "skills" if project_path else None

    def load(self, ref: str) -> SkillDocument:
        if not _SAFE_REF.fullmatch(ref):
            raise ValueError(f"Skill ref 不是安全标识: {ref}")
        candidates = []
        if self._project is not None:
            candidates.append((self._project / f"{ref}.md", "project"))
        candidates.append((self._builtin / f"{ref}.md", "builtin"))
        for path, source in candidates:
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                if len(content) > 16_000:
                    raise ValueError(f"Skill '{ref}' 超过 16000 字符限制")
                title = content.splitlines()[0].lstrip("# ").strip() or ref
                return SkillDocument(ref=ref, title=title, content=content, source=source)
        raise FileNotFoundError(f"未找到 Skill: {ref}")

    def load_many(self, refs: tuple[str, ...] | list[str]) -> tuple[SkillDocument, ...]:
        documents: list[SkillDocument] = []
        for ref in dict.fromkeys(refs):
            documents.append(self.load(ref))
        return tuple(documents)

    def render(self, refs: tuple[str, ...] | list[str]) -> str:
        documents = self.load_many(refs)
        if not documents:
            return ""
        return "\n\n".join(document.as_prompt() for document in documents)

    def refs(self) -> tuple[str, ...]:
        names = {path.stem for path in self._builtin.glob("*.md")}
        if self._project is not None and self._project.is_dir():
            names.update(path.stem for path in self._project.glob("*.md"))
        return tuple(sorted(names))
