"""Persistent memory for BYOA agents."""

import json
import os
from pathlib import Path
from datetime import datetime


class AgentMemory:
    """Persistent memory store for an agent."""

    def __init__(self, agent_name: str, memory_dir: str = "./memory"):
        self.agent_name = agent_name
        self.base_dir = Path(memory_dir) / agent_name.lower().replace(" ", "-")
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Three types of memory
        self.conversations_file = self.base_dir / "conversations.jsonl"
        self.facts_file = self.base_dir / "facts.json"
        self.knowledge_dir = self.base_dir / "knowledge"
        self.knowledge_dir.mkdir(exist_ok=True)

        self._facts = self._load_facts()

    # --- Conversation History (per room) ---

    def save_message(self, room_id: str, role: str, content: str):
        """Append a message to conversation history."""
        entry = {
            "room_id": room_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        with open(self.conversations_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get_recent_messages(self, room_id: str, limit: int = 20) -> list[dict]:
        """Get recent messages for a room."""
        if not self.conversations_file.exists():
            return []

        messages = []
        with open(self.conversations_file) as f:
            for line in f:
                entry = json.loads(line.strip())
                if entry["room_id"] == room_id:
                    messages.append({
                        "role": entry["role"],
                        "content": entry["content"],
                    })

        return messages[-limit:]

    # --- Facts (long-term memory) ---

    def _load_facts(self) -> dict:
        if self.facts_file.exists():
            return json.loads(self.facts_file.read_text())
        return {"people": {}, "preferences": {}, "decisions": [], "notes": []}

    def _save_facts(self):
        self.facts_file.write_text(json.dumps(self._facts, indent=2))

    def remember_person(self, name: str, info: dict):
        """Remember info about a person."""
        if name not in self._facts["people"]:
            self._facts["people"][name] = {}
        self._facts["people"][name].update(info)
        self._facts["people"][name]["last_updated"] = datetime.now().isoformat()
        self._save_facts()

    def remember_preference(self, key: str, value: str):
        """Remember a preference or setting."""
        self._facts["preferences"][key] = {
            "value": value,
            "set_at": datetime.now().isoformat(),
        }
        self._save_facts()

    def remember_decision(self, decision: str):
        """Log a decision."""
        self._facts["decisions"].append({
            "decision": decision,
            "timestamp": datetime.now().isoformat(),
        })
        self._save_facts()

    def add_note(self, note: str):
        """Add a general note."""
        self._facts["notes"].append({
            "note": note,
            "timestamp": datetime.now().isoformat(),
        })
        self._save_facts()

    def get_context_summary(self) -> str:
        """Build a context string from all stored facts."""
        parts = []

        if self._facts["people"]:
            parts.append("**People I know:**")
            for name, info in self._facts["people"].items():
                details = ", ".join(f"{k}: {v}" for k, v in info.items() if k != "last_updated")
                parts.append(f"- {name}: {details}")

        if self._facts["preferences"]:
            parts.append("\n**Preferences:**")
            for key, val in self._facts["preferences"].items():
                parts.append(f"- {key}: {val['value']}")

        if self._facts["decisions"]:
            parts.append("\n**Recent decisions:**")
            for d in self._facts["decisions"][-10:]:
                parts.append(f"- {d['decision']}")

        if self._facts["notes"]:
            parts.append("\n**Notes:**")
            for n in self._facts["notes"][-10:]:
                parts.append(f"- {n['note']}")

        return "\n".join(parts) if parts else ""

    # --- Knowledge Base (files) ---

    def add_knowledge(self, filename: str, content: str):
        """Add a knowledge document."""
        filepath = self.knowledge_dir / filename
        filepath.write_text(content)

    def get_knowledge(self) -> list[dict]:
        """Get all knowledge documents."""
        docs = []
        for f in self.knowledge_dir.iterdir():
            if f.is_file():
                docs.append({
                    "filename": f.name,
                    "content": f.read_text(),
                })
        return docs

    def get_knowledge_summary(self) -> str:
        """Build context from knowledge files."""
        docs = self.get_knowledge()
        if not docs:
            return ""
        parts = ["**Knowledge base:**"]
        for doc in docs:
            parts.append(f"\n--- {doc['filename']} ---\n{doc['content']}")
        return "\n".join(parts)
