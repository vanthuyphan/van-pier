"""Task runner — a leader agent that orchestrates multi-agent tasks."""

import asyncio
import anthropic
from .tasks import TaskManager, Task, TaskStatus, StepStatus


class TaskRunner:
    """Runs tasks by coordinating agents. One agent is the leader."""

    def __init__(self, task_manager: TaskManager, runtime):
        self.tm = task_manager
        self.runtime = runtime
        self.client = anthropic.AsyncAnthropic()

    async def plan_task(self, description: str, room_id: str, created_by: str) -> Task:
        """Use an LLM to break a task into steps and assign agents."""
        available_agents = []
        for username, bot in self.runtime.bots.items():
            agent = bot["agent"]
            available_agents.append({
                "username": username,
                "name": agent.config.name,
                "role": agent.config.system_prompt[:200],
                "tools": agent.config.tools,
            })

        agents_desc = "\n".join(
            f"- @{a['username']}: {a['name']} — {a['role'][:100]}... (tools: {a['tools']})"
            for a in available_agents
        )

        resp = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": description}],
            system=(
                "You are a task planner. Break the user's request into steps and assign each step to an agent.\n\n"
                f"Available agents:\n{agents_desc}\n\n"
                "Respond ONLY with valid JSON, no other text:\n"
                "{\n"
                '  "title": "short title",\n'
                '  "steps": [\n'
                '    {"id": "step-1", "agent": "agent-username", "instruction": "what to do", "depends_on": [], "requires_approval": false},\n'
                '    {"id": "step-2", "agent": "agent-username", "instruction": "what to do", "depends_on": ["step-1"], "requires_approval": true}\n'
                "  ]\n"
                "}\n\n"
                "Rules:\n"
                "- Use depends_on to sequence steps that need previous output\n"
                "- Steps with no dependencies can run in parallel\n"
                "- Set requires_approval: true for actions that send/publish/delete\n"
                "- Pick the best agent for each step based on their role\n"
                "- Keep it practical — 2-5 steps max\n"
            ),
        )

        import json
        text = resp.content[0].text.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        plan = json.loads(text)

        task = self.tm.create_task(
            title=plan["title"],
            description=description,
            created_by=created_by,
            room_id=room_id,
            steps=plan["steps"],
        )
        return task

    async def run_task(self, task_id: str, send_message):
        """Execute a task step by step. send_message(room_id, text) posts to chat."""
        task = self.tm.get_task(task_id)
        if not task:
            return

        self.tm.start_task(task_id)
        await send_message(task.room_id, self.tm.format_task_card(task))

        while not task.is_done() and task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            next_steps = task.next_steps()
            if not next_steps:
                # Might be waiting for approval
                if any(s.status == StepStatus.WAITING_APPROVAL for s in task.steps):
                    await asyncio.sleep(2)
                    task = self.tm.get_task(task_id)  # Reload
                    continue
                break

            # Run ready steps in parallel
            step_tasks = []
            for step in next_steps:
                step_tasks.append(self._run_step(task, step, send_message))

            await asyncio.gather(*step_tasks)
            task = self.tm.get_task(task_id)  # Reload after steps complete

        # Final update
        task = self.tm.get_task(task_id)
        await send_message(task.room_id, self.tm.format_task_card(task))

        if task.status == TaskStatus.COMPLETED:
            # Ask leader to summarize
            summary = await self._summarize_task(task)
            await send_message(task.room_id, f"**Task Complete: {task.title}**\n\n{summary}")

    async def _run_step(self, task: Task, step, send_message):
        """Run a single step by asking the assigned agent."""
        self.tm.set_step_running(task.id, step.id)

        # Get the agent
        bot = self.runtime.bots.get(step.agent)
        if not bot:
            self.tm.fail_step(task.id, step.id, f"Agent @{step.agent} not found")
            return

        agent = bot["agent"]
        client = bot["client"]

        # Build context from previous step outputs
        context_parts = []
        for prev in task.steps:
            if prev.id in step.depends_on and prev.output:
                context_parts.append(f"Output from @{prev.agent} (step {prev.id}):\n{prev.output}")

        context = "\n\n".join(context_parts) if context_parts else ""

        prompt = step.instruction
        if context:
            prompt = f"{step.instruction}\n\nContext from previous steps:\n{context}"

        # Post to chat that this step is starting
        await send_message(
            task.room_id,
            f"🔄 **@{step.agent}** working on: _{step.instruction}_"
        )

        try:
            replies = await agent.respond(task.room_id, "task-runner", prompt)
            output = "\n".join(replies)

            if step.requires_approval:
                self.tm.set_step_waiting(task.id, step.id)
                # Post the output and ask for approval
                for reply in replies:
                    await send_message(task.room_id, f"**@{step.agent}:**\n{reply}")
                await send_message(
                    task.room_id,
                    f"⏸️ **Approval needed** for step `{step.id}`\n"
                    f"Reply `approve {task.id} {step.id}` or `reject {task.id} {step.id}`"
                )
                # Wait for approval (poll)
                while True:
                    await asyncio.sleep(2)
                    t = self.tm.get_task(task.id)
                    for s in t.steps:
                        if s.id == step.id:
                            if s.status == StepStatus.COMPLETED:
                                return
                            if s.status == StepStatus.FAILED:
                                return
                            break
            else:
                self.tm.complete_step(task.id, step.id, output)
                for reply in replies:
                    await send_message(task.room_id, f"**@{step.agent}:**\n{reply}")

        except Exception as e:
            self.tm.fail_step(task.id, step.id, str(e))
            await send_message(task.room_id, f"❌ **@{step.agent}** failed: {e}")

    async def _summarize_task(self, task: Task) -> str:
        """Summarize all step outputs into a final result."""
        parts = []
        for step in task.steps:
            if step.output:
                parts.append(f"@{step.agent}: {step.output[:300]}")

        all_outputs = "\n\n".join(parts)

        resp = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": f"Summarize the results of this completed task:\n\nTask: {task.title}\n{task.description}\n\nOutputs:\n{all_outputs}"}],
            system="You are a task summarizer. Give a brief, clear summary of what was accomplished. Use bullet points.",
        )
        return resp.content[0].text
