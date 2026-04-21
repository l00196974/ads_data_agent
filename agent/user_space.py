from pathlib import Path


class UserSpace:
    def __init__(self, user_id: str, data_dir: str = "./data"):
        self.user_id = user_id
        self.data_dir = Path(data_dir) / user_id
        self.skills_dir = self.data_dir / "skills"
        self.memory_dir = self.data_dir / "memory"
        self.agents_md_path = self.data_dir / "agents.md"
        self._ensure_dirs()

    def _ensure_dirs(self):
        for d in [self.data_dir, self.skills_dir, self.memory_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def get_agents_md(self, system_md_path: str = "config/system_agents.md") -> str:
        parts = []
        sys_path = Path(system_md_path)
        if sys_path.exists():
            parts.append(sys_path.read_text())
        if self.agents_md_path.exists():
            user_content = self.agents_md_path.read_text().strip()
            if user_content:
                parts.append("\n\n## 用户自定义指令\n" + user_content)
        return "\n\n".join(parts)
