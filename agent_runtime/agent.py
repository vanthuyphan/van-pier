"""Agent that connects an .md config to an LLM and Matrix room."""

import json
import anthropic
from .md_parser import AgentConfig
from .approval import ApprovalManager, ApprovalStatus
from .tools import TOOL_DEFINITIONS, execute_tool
from .memory import AgentMemory


class Agent:
    def __init__(self, config: AgentConfig, approval_manager: ApprovalManager):
        self.config = config
        self.approval = approval_manager
        self.client = anthropic.AsyncAnthropic()
        self.memory = AgentMemory(config.name)  # Only for facts/knowledge, NOT conversations
        self.pending_tool_calls: dict[str, dict] = {}
        # In-memory conversation cache (Matrix is the source of truth)
        self._history: dict[str, list] = {}
        self._summaries: dict[str, str] = {}  # room_id -> summary of older messages

    def add_to_history(self, room_id: str, role: str, content: str):
        """Add a message to the in-memory history for a room."""
        if room_id not in self._history:
            self._history[room_id] = []
        self._history[room_id].append({"role": role, "content": content})
        # Keep only last 50 raw messages
        if len(self._history[room_id]) > 50:
            self._history[room_id] = self._history[room_id][-50:]

    def get_history(self, room_id: str) -> list:
        recent = self._history.get(room_id, [])[-10:]
        # If we have a summary, prepend it as context
        if room_id in self._summaries:
            summary_msg = {"role": "user", "content": f"[system]: Summary of earlier conversation:\n{self._summaries[room_id]}"}
            return [summary_msg] + recent
        return recent

    async def _maybe_summarize(self, room_id: str):
        """If history is long, summarize older messages to save tokens."""
        history = self._history.get(room_id, [])
        if len(history) < 30:
            return

        # Take the oldest 20 messages and summarize them
        old_messages = history[:20]
        old_text = "\n".join(m["content"] for m in old_messages)

        try:
            resp = await self.client.messages.create(
                model="claude-haiku-4-5-20251001",  # Use cheapest model for summaries
                max_tokens=300,
                messages=[{"role": "user", "content": f"Summarize this conversation in 3-5 bullet points. Focus on key facts, decisions, and action items:\n\n{old_text}"}],
            )
            summary = resp.content[0].text
            self._summaries[room_id] = summary
            # Remove the old messages we just summarized
            self._history[room_id] = history[20:]
        except Exception as e:
            print(f"  Summary failed for {self.config.name}: {e}")

    async def observe(self, room_id: str, sender: str, message: str):
        """Observe a message without responding — builds room context."""
        self.add_to_history(room_id, "user", f"[{sender}]: {message}")

    async def respond(self, room_id: str, user_name: str, message: str) -> list[str]:
        """Generate a response. Returns list of messages to send."""
        self.add_to_history(room_id, "user", f"[{user_name}]: {message}")

        recent = self.get_history(room_id)
        tools = self._get_tools()

        kwargs = dict(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=self._build_system_prompt(),
            messages=recent,
        )
        if tools:
            kwargs["tools"] = tools

        response = await self.client.messages.create(**kwargs)

        replies = []
        tool_use_blocks = []

        for block in response.content:
            if block.type == "text" and block.text.strip():
                replies.append(block.text)
            elif block.type == "tool_use":
                tool_use_blocks.append(block)

        for tool_block in tool_use_blocks:
            tool_name = tool_block.name
            tool_input = tool_block.input

            if tool_name == "send_email":
                draft = (
                    f"**I'd like to send this email:**\n\n"
                    f"**To:** {tool_input.get('to', 'N/A')}\n"
                    f"**Subject:** {tool_input.get('subject', 'N/A')}\n\n"
                    f"---\n{tool_input.get('body', '')}\n---"
                )
                replies.append(draft)
                action_id = f"email-{id(tool_block)}"
                self.pending_tool_calls[action_id] = {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "room_id": room_id,
                }
                replies.append(f"\nReply **`send`** to send it, or tell me what to change.")

            elif tool_name == "draft_email":
                result = execute_tool(tool_name, tool_input)
                replies.append(result.message)
                action_id = f"email-{id(tool_block)}"
                self.pending_tool_calls[action_id] = {
                    "tool_name": "send_email",
                    "tool_input": tool_input,
                    "room_id": room_id,
                }
                replies.append(f"\nReply **`send`** to send it, or tell me what to change.")

            elif tool_name == "web_search":
                result = execute_tool(tool_name, tool_input)
                replies.append(f"*Searched: {tool_input.get('query')}*\n\n{result.message}")

            elif tool_name == "remember":
                self._handle_remember(tool_input)

            elif tool_name == "recall":
                pass

        # Save assistant response to history
        if replies:
            combined = "\n\n".join(replies)
            self.add_to_history(room_id, "assistant", combined)

        # Auto-summarize if history is getting long
        await self._maybe_summarize(room_id)

        return replies

    def _handle_remember(self, tool_input: dict):
        mem_type = tool_input.get("type", "note")
        key = tool_input.get("key", "")
        value = tool_input.get("value", "")

        if mem_type == "person":
            self.memory.remember_person(key, {"info": value})
        elif mem_type == "preference":
            self.memory.remember_preference(key, value)
        elif mem_type == "decision":
            self.memory.remember_decision(value)
        elif mem_type == "note":
            self.memory.add_note(value)

    async def handle_send_command(self, room_id: str) -> str:
        latest_action_id = None
        for action_id, info in self.pending_tool_calls.items():
            if info["room_id"] == room_id:
                latest_action_id = action_id

        if not latest_action_id:
            return "No pending email to send."

        info = self.pending_tool_calls.pop(latest_action_id)
        result = execute_tool(info["tool_name"], info["tool_input"])

        if result.success:
            return f"Done! {result.message}"
        else:
            return f"Failed: {result.message}"

    def _get_tools(self) -> list:
        agent_tools = self.config.tools or []
        available = []
        tool_mapping = {
            "email": ["send_email", "draft_email"],
            "draft_email": ["draft_email"],
            "send_email": ["send_email"],
            "web_search": ["web_search"],
            "memory": ["remember", "recall"],
        }
        all_tools = list(agent_tools) + ["memory"]
        for tool_name in all_tools:
            mapped = tool_mapping.get(tool_name, [])
            for m in mapped:
                for defn in TOOL_DEFINITIONS:
                    if defn["name"] == m and defn not in available:
                        available.append(defn)
        return available

    def _build_system_prompt(self) -> str:
        memory_context = self.memory.get_context_summary()
        knowledge_context = self.memory.get_knowledge_summary()

        memory_section = ""
        if memory_context:
            memory_section += f"\n\nYour long-term memory:\n{memory_context}\n"
        if knowledge_context:
            memory_section += f"\n{knowledge_context}\n"

        return (
            f"You are '{self.config.name}', an AI agent in a team chat room.\n"
            f"Multiple humans and agents share this room.\n"
            f"Messages are prefixed with [username] so you know who is speaking.\n"
            f"You can see ALL messages in the room, not just ones directed at you.\n"
            f"{memory_section}\n"
            f"Your instructions:\n{self.config.system_prompt}\n\n"
            f"Guidelines:\n"
            f"- Be concise. You're in a chat, not writing an essay.\n"
            f"- Stay in your lane — only respond to things relevant to your role.\n"
            f"- When asked to draft or send an email, use the appropriate tool.\n"
            f"- ALWAYS use the 'remember' tool automatically when you learn something important:\n"
            f"  - A person's name, role, email, or preferences\n"
            f"  - A decision that was made\n"
            f"  - A preference about tone, style, or process\n"
            f"  - Any context that would be useful in future conversations\n"
            f"  Do NOT wait to be asked — proactively remember.\n"
            f"- Use markdown for formatting.\n"
        )

    def should_respond(self, message: str) -> bool:
        if self.config.trigger == "all":
            return True
        if self.config.trigger == "mention":
            msg = message.lower()
            name_lower = self.config.name.lower()
            username_lower = self.config.username.lower()
            return (
                f"@{name_lower}" in msg
                or f"@{username_lower}" in msg
                or name_lower in msg
                or username_lower in msg
            )
        return False
