"""
太一 (The One) 5-Layer Memory (Simplified for Demo)

L1: 短期上下文 (in-memory dict)
L2: SKILL.md 程序性知识 (Markdown files on disk)
L3: 向量索引 (简化:关键词相似度)
L4: Honcho 用户建模 (简化:preference dict + 辩证合并)
L5: FTS5 全文检索 (简化:memory/*.md 文件 grep)
"""
from __future__ import annotations
import os
import re
import json
import time
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field


@dataclass
class MemoryHit:
    layer: str
    content: str
    source: str
    score: float = 0.0


class OneMemory:
    """3 层记忆(简化版,L1/L2/L4)"""

    def __init__(self, base_dir: str = "/tmp/helix_demo"):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / "memory").mkdir(exist_ok=True)
        (self.base / "skills").mkdir(exist_ok=True)
        (self.base / "scenarios").mkdir(exist_ok=True)
        self._seed_defaults()

    def _seed_defaults(self):
        """首次初始化时,从打包预设复制默认 Skill / Scenario"""
        # 预设路径:优先 __file__ 路径,回退到环境变量,再回退到 /workspace
        candidates = [
            Path(__file__).resolve().parent.parent,                          # 源码路径
            Path(os.environ.get("HELIX_DEMO_PRESET", "/workspace/agent-arch/demo")),
        ]
        preset_root = None
        for c in candidates:
            if (c / "skills").exists() and (c / "scenarios").exists():
                preset_root = c
                break
        if preset_root is None:
            return

        for sub in ("skills", "scenarios"):
            src = preset_root / sub
            dst = self.base / sub
            if not src.exists():
                continue
            for p in src.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(src)
                    target = dst / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        target.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

        # L1: 短期上下文
        self.l1_short_term: dict[str, list[dict]] = {}

        # L4: 用户模型(Honcho 简化)
        self.l4_user_model: dict[str, str] = {
            "name": "unknown",
            "preferences": "",
        }

    # ====== L1: 短期上下文 ======
    def l1_add(self, session_id: str, role: str, content: str) -> None:
        self.l1_short_term.setdefault(session_id, []).append({
            "role": role, "content": content, "ts": time.time()
        })

    def l1_get(self, session_id: str) -> list[dict]:
        return list(self.l1_short_term.get(session_id, []))

    # ====== L2: SKILL.md 程序性知识 ======
    def l2_load_skill(self, skill_name: str) -> dict | None:
        path = self.base / "skills" / skill_name / "SKILL.md"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        # 简化:解析 frontmatter
        meta, body = self._parse_md(text)
        qg_path = self.base / "skills" / skill_name / "quality_gate.md"
        quality_gate = qg_path.read_text(encoding="utf-8") if qg_path.exists() else None
        return {
            "name": skill_name,
            "frontmatter": meta,
            "body": body,
            "quality_gate": quality_gate,
            "path": str(path),
        }

    def l2_list_skills(self) -> list[str]:
        skills = []
        for p in (self.base / "skills").iterdir():
            if p.is_dir() and (p / "SKILL.md").exists():
                skills.append(p.name)
        return sorted(skills)

    def l2_save_skill(self, skill_name: str, body: str, quality_gate: str | None = None) -> None:
        skill_dir = self.base / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\n---\n\n{body}", encoding="utf-8"
        )
        if quality_gate:
            (skill_dir / "quality_gate.md").write_text(quality_gate, encoding="utf-8")

    # ====== L3: 简化向量检索(关键词相似度) ======
    def l3_search(self, query: str, top_k: int = 3) -> list[MemoryHit]:
        # 简化:对所有 SKILL.md 文本做关键词命中打分
        hits = []
        q_words = set(re.findall(r"\w+", query.lower()))
        for skill in self.l2_list_skills():
            skill_data = self.l2_load_skill(skill)
            if not skill_data:
                continue
            text = (skill_data.get("body") or "").lower()
            t_words = set(re.findall(r"\w+", text))
            if not q_words or not t_words:
                continue
            score = len(q_words & t_words) / max(len(q_words), 1)
            if score > 0:
                hits.append(MemoryHit(
                    layer="L3", source=skill, content=skill_data["body"],
                    score=score,
                ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    # ====== L4: Honcho 用户建模(辩证法简化) ======
    def l4_observe(self, new_observation: str) -> str:
        """
        简化版的"正-反-合"辩证:
        - thesis:当前偏好
        - antithesis:新观察
        - synthesis:合并后
        """
        thesis = self.l4_user_model.get("preferences", "")
        antithesis = new_observation
        if not thesis:
            synthesis = antithesis
        elif antithesis.lower() in thesis.lower():
            synthesis = thesis  # 已存在,无需更新
        else:
            synthesis = f"{thesis}\n- {antithesis}"
        self.l4_user_model["preferences"] = synthesis
        return synthesis

    def l4_get(self) -> str:
        return self.l4_user_model.get("preferences", "")

    # ====== L5: FTS5 全文检索(简化:Markdown grep) ======
    def l5_search(self, query: str) -> list[MemoryHit]:
        hits = []
        for md_file in (self.base / "memory").glob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            if query.lower() in text.lower():
                hits.append(MemoryHit(layer="L5", content=text, source=md_file.name, score=1.0))
        return hits

    def l5_log(self, content: str) -> None:
        today = time.strftime("%Y-%m-%d")
        path = self.base / "memory" / f"{today}.md"
        ts = time.strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n\n{content}\n")

    # ====== 工具方法 ======
    def _parse_md(self, text: str) -> tuple[dict, str]:
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                meta_raw = parts[1].strip()
                body = parts[2].strip()
                meta = {}
                for line in meta_raw.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                return meta, body
        return {}, text

    # ====== 场景库 ======
    def load_scenario(self, name: str) -> dict | None:
        path = self.base / "scenarios" / f"{name}.md"
        if not path.exists():
            return None
        return {
            "name": name,
            "raw": path.read_text(encoding="utf-8"),
            "path": str(path),
        }

    def list_scenarios(self) -> list[str]:
        return sorted(p.stem for p in (self.base / "scenarios").glob("*.md"))
