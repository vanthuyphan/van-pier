"""Audit logger for all agent actions."""

import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class AuditEntry:
    timestamp: float
    agent_name: str
    event_type: str  # "message", "tool_call", "approval", "error", "joined_room"
    room_id: str
    user: str
    detail: str
    status: str = "ok"  # "ok", "pending", "approved", "rejected", "error"


class AuditLog:
    def __init__(self, log_dir: str = "./audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "events.jsonl"
        self._recent: list[dict] = []
        self._load_recent()

    def _load_recent(self):
        """Load last 500 entries into memory for the dashboard."""
        if not self.log_file.exists():
            return
        lines = self.log_file.read_text().strip().split("\n")
        for line in lines[-500:]:
            if line:
                self._recent.append(json.loads(line))

    def log(self, agent_name: str, event_type: str, room_id: str,
            user: str, detail: str, status: str = "ok"):
        entry = AuditEntry(
            timestamp=time.time(),
            agent_name=agent_name,
            event_type=event_type,
            room_id=room_id,
            user=user,
            detail=detail,
            status=status,
        )
        record = asdict(entry)
        with open(self.log_file, "a") as f:
            f.write(json.dumps(record) + "\n")
        self._recent.append(record)
        if len(self._recent) > 500:
            self._recent = self._recent[-500:]

    def get_recent(self, limit: int = 100, agent_name: str = None,
                   event_type: str = None) -> list[dict]:
        results = self._recent
        if agent_name:
            results = [r for r in results if r["agent_name"] == agent_name]
        if event_type:
            results = [r for r in results if r["event_type"] == event_type]
        return results[-limit:]

    def get_agent_stats(self) -> dict:
        """Get per-agent statistics."""
        stats = {}
        for entry in self._recent:
            name = entry["agent_name"]
            if name not in stats:
                stats[name] = {
                    "messages": 0,
                    "tool_calls": 0,
                    "approvals_pending": 0,
                    "approvals_approved": 0,
                    "approvals_rejected": 0,
                    "errors": 0,
                    "last_active": 0,
                }
            s = stats[name]
            s["last_active"] = max(s["last_active"], entry["timestamp"])

            if entry["event_type"] == "message":
                s["messages"] += 1
            elif entry["event_type"] == "tool_call":
                s["tool_calls"] += 1
            elif entry["event_type"] == "approval":
                if entry["status"] == "pending":
                    s["approvals_pending"] += 1
                elif entry["status"] == "approved":
                    s["approvals_approved"] += 1
                elif entry["status"] == "rejected":
                    s["approvals_rejected"] += 1
            elif entry["event_type"] == "error":
                s["errors"] += 1
        return stats
