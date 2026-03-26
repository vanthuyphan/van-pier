"""Parse .md agent definition files into agent configs."""

import yaml
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    name: str
    avatar: str
    trigger: str  # "mention", "all", "schedule:HH:MM"
    tools: list[str] = field(default_factory=list)
    approval: str = "required"  # "required", "not_required"
    author: str = ""
    mcp_servers: dict = field(default_factory=dict)  # name -> {command, args, env}
    system_prompt: str = ""
    source_file: str = ""

    @property
    def username(self) -> str:
        return self.name.lower().replace(" ", "-").replace("_", "-")

    @property
    def display_name(self) -> str:
        return f"{self.avatar} {self.name}"


def parse_agent_md(file_path: str | Path) -> AgentConfig:
    """Parse a .md agent file into an AgentConfig."""
    path = Path(file_path)
    content = path.read_text()

    # Split frontmatter from body
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            body = parts[2].strip()
        else:
            raise ValueError(f"Invalid frontmatter in {file_path}")
    else:
        raise ValueError(f"No frontmatter found in {file_path}")

    return AgentConfig(
        name=frontmatter.get("name", path.stem),
        avatar=frontmatter.get("avatar", "\U0001F916"),
        trigger=frontmatter.get("trigger", "mention"),
        tools=frontmatter.get("tools", []),
        approval=frontmatter.get("approval", "required"),
        author=frontmatter.get("author", ""),
        mcp_servers=frontmatter.get("mcp_servers", {}),
        system_prompt=body,
        source_file=str(path),
    )


def load_all_agents(agents_dir: str | Path) -> list[AgentConfig]:
    """Load all .md agent definitions from a directory."""
    agents_path = Path(agents_dir)
    configs = []
    for md_file in sorted(agents_path.glob("*.md")):
        try:
            config = parse_agent_md(md_file)
            configs.append(config)
            print(f"  Loaded agent: {config.display_name} from {md_file.name}")
        except Exception as e:
            print(f"  Failed to load {md_file.name}: {e}")
    return configs
