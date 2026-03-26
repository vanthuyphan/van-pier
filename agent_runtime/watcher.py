"""Watch the agents/ directory for new or changed .md files and hot-reload."""

import asyncio
from pathlib import Path
from watchfiles import awatch, Change

from .md_parser import parse_agent_md


async def watch_agents(agents_dir: str, on_change):
    """Watch for .md file changes in the agents directory."""
    print(f"Watching {agents_dir} for agent changes...")

    async for changes in awatch(agents_dir):
        for change_type, path in changes:
            if not path.endswith(".md"):
                continue

            if change_type == Change.added:
                print(f"\n  New agent detected: {Path(path).name}")
                try:
                    config = parse_agent_md(path)
                    await on_change("added", config)
                except Exception as e:
                    print(f"  Failed to load {path}: {e}")

            elif change_type == Change.modified:
                print(f"\n  Agent updated: {Path(path).name}")
                try:
                    config = parse_agent_md(path)
                    await on_change("modified", config)
                except Exception as e:
                    print(f"  Failed to reload {path}: {e}")

            elif change_type == Change.deleted:
                print(f"\n  Agent removed: {Path(path).name}")
                await on_change("deleted", Path(path).stem)
