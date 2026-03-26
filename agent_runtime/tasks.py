"""Task system — assign multi-agent tasks with steps and checkpoints."""

import json
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class TaskStep:
    id: str
    agent: str  # agent username
    instruction: str
    status: StepStatus = StepStatus.PENDING
    requires_approval: bool = False
    depends_on: list[str] = field(default_factory=list)  # step IDs
    output: str = ""
    started_at: float = 0
    completed_at: float = 0


@dataclass
class Task:
    id: str
    title: str
    description: str
    created_by: str
    room_id: str
    status: TaskStatus = TaskStatus.PENDING
    steps: list[TaskStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0

    def next_steps(self) -> list[TaskStep]:
        """Get steps that are ready to run (dependencies met)."""
        completed_ids = {s.id for s in self.steps if s.status == StepStatus.COMPLETED}
        ready = []
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            if all(dep in completed_ids for dep in step.depends_on):
                ready.append(step)
        return ready

    def is_done(self) -> bool:
        return all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in self.steps)

    def progress(self) -> str:
        done = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        total = len(self.steps)
        return f"{done}/{total}"


class TaskManager:
    def __init__(self, store_dir: str = "./tasks"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.tasks: dict[str, Task] = {}
        self._load()

    def _load(self):
        for f in self.store_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                steps = [TaskStep(**s) for s in data.pop("steps", [])]
                task = Task(**data, steps=steps)
                self.tasks[task.id] = task
            except Exception as e:
                print(f"  Failed to load task {f}: {e}")

    def _save(self, task: Task):
        data = asdict(task)
        filepath = self.store_dir / f"{task.id}.json"
        filepath.write_text(json.dumps(data, indent=2))

    def create_task(self, title: str, description: str, created_by: str,
                    room_id: str, steps: list[dict]) -> Task:
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task_steps = []
        for i, s in enumerate(steps):
            step = TaskStep(
                id=s.get("id", f"step-{i+1}"),
                agent=s["agent"],
                instruction=s["instruction"],
                requires_approval=s.get("requires_approval", False),
                depends_on=s.get("depends_on", []),
            )
            task_steps.append(step)

        task = Task(
            id=task_id,
            title=title,
            description=description,
            created_by=created_by,
            room_id=room_id,
            steps=task_steps,
        )
        self.tasks[task_id] = task
        self._save(task)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def list_tasks(self, room_id: str = None) -> list[Task]:
        tasks = list(self.tasks.values())
        if room_id:
            tasks = [t for t in tasks if t.room_id == room_id]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def start_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus.RUNNING
            self._save(task)

    def complete_step(self, task_id: str, step_id: str, output: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.id == step_id:
                step.status = StepStatus.COMPLETED
                step.output = output
                step.completed_at = time.time()
                break
        if task.is_done():
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
        self._save(task)

    def fail_step(self, task_id: str, step_id: str, error: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.id == step_id:
                step.status = StepStatus.FAILED
                step.output = error
                step.completed_at = time.time()
                break
        task.status = TaskStatus.FAILED
        self._save(task)

    def set_step_running(self, task_id: str, step_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.id == step_id:
                step.status = StepStatus.RUNNING
                step.started_at = time.time()
                break
        self._save(task)

    def set_step_waiting(self, task_id: str, step_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.id == step_id:
                step.status = StepStatus.WAITING_APPROVAL
                break
        task.status = TaskStatus.WAITING_APPROVAL
        self._save(task)

    def approve_step(self, task_id: str, step_id: str):
        task = self.tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.id == step_id:
                step.status = StepStatus.COMPLETED
                step.completed_at = time.time()
                break
        task.status = TaskStatus.RUNNING
        self._save(task)

    def format_task_card(self, task: Task) -> str:
        """Format a task as a chat message."""
        status_emoji = {
            TaskStatus.PENDING: "⏳",
            TaskStatus.RUNNING: "🔄",
            TaskStatus.WAITING_APPROVAL: "⏸️",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.FAILED: "❌",
            TaskStatus.CANCELLED: "🚫",
        }
        step_emoji = {
            StepStatus.PENDING: "⬜",
            StepStatus.RUNNING: "🔄",
            StepStatus.WAITING_APPROVAL: "⏸️",
            StepStatus.COMPLETED: "✅",
            StepStatus.SKIPPED: "⏭️",
            StepStatus.FAILED: "❌",
        }

        lines = [
            f"**{status_emoji.get(task.status, '')} Task: {task.title}** [{task.progress()}]",
            f"_{task.description}_",
            "",
        ]
        for step in task.steps:
            emoji = step_emoji.get(step.status, "⬜")
            agent_label = f"@{step.agent}"
            line = f"{emoji} {agent_label}: {step.instruction}"
            if step.output and step.status == StepStatus.COMPLETED:
                preview = step.output[:100]
                line += f"\n   ↳ _{preview}_"
            if step.status == StepStatus.WAITING_APPROVAL:
                line += f"\n   ↳ **Waiting for approval** — reply `approve {task.id} {step.id}`"
            lines.append(line)

        return "\n".join(lines)
