#!/usr/bin/env python3
"""
BYOA Demo - Bring Your Own Agent
A local chat simulation to test .md agents without Matrix.
Run: python3 demo.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from agent_runtime.md_parser import load_all_agents, AgentConfig
from agent_runtime.approval import ApprovalManager

try:
    import anthropic
except ImportError:
    print("Installing dependencies...")
    os.system(f"{sys.executable} -m pip install anthropic pyyaml")
    import anthropic


class DemoChat:
    def __init__(self, agents_dir: str):
        self.agents_dir = agents_dir
        self.approval_manager = ApprovalManager()
        self.agents: dict[str, dict] = {}
        self.client = anthropic.Anthropic()
        self.history: dict[str, list] = {}  # agent_name -> conversation

    def load_agents(self):
        print("\n  Loading agents from agents/\n")
        configs = load_all_agents(self.agents_dir)
        for config in configs:
            self.agents[config.name.lower()] = {
                "config": config,
                "history": [],
            }
        return configs

    def respond(self, agent_name: str, user_message: str) -> str:
        agent = self.agents[agent_name.lower()]
        config: AgentConfig = agent["config"]
        history: list = agent["history"]

        history.append({"role": "user", "content": f"[human]: {user_message}"})

        system_prompt = (
            f"You are '{config.name}', an AI agent in a team chat room.\n"
            f"You are chatting with a human coworker.\n\n"
            f"Your instructions:\n{config.system_prompt}\n\n"
            f"Be concise — you're in a chat, not writing an essay.\n"
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=history[-20:],
        )

        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})

        # Check if approval is needed
        if config.approval == "required" and self._looks_like_action(reply):
            action = self.approval_manager.create_action(
                config.name, "demo-room", reply
            )
            return reply + "\n\n" + self.approval_manager.format_approval_message(action)

        return reply

    def _looks_like_action(self, text: str) -> bool:
        indicators = [
            "i'll send", "i will send", "i'll create", "i will create",
            "i'll post", "i will post", "here's the draft", "here is the draft",
            "sending email", "drafting email",
        ]
        text_lower = text.lower()
        return any(ind in text_lower for ind in indicators)

    def run(self):
        configs = self.load_agents()
        if not configs:
            print("No agents found in agents/ directory.")
            return

        print("\n" + "=" * 50)
        print("  BYOA Demo Chat")
        print("=" * 50)
        print("\n  Available agents:\n")
        for config in configs:
            print(f"    {config.display_name}")
            print(f"      Trigger: @mention | Approval: {config.approval}")
            print(f"      File: {config.source_file}")
            print()

        print("  How to use:")
        print("    @sales helper <message>    — talk to Sales Helper")
        print("    @code reviewer <message>   — talk to Code Reviewer")
        print("    @standup bot <message>     — talk to Standup Bot")
        print("    approve <action-id>        — approve an action")
        print("    reject <action-id>         — reject an action")
        print("    /agents                    — list agents")
        print("    /quit                      — exit")
        print()
        print("-" * 50)

        while True:
            try:
                user_input = input("\n  you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\nBye!")
                break

            if not user_input:
                continue

            if user_input == "/quit":
                print("Bye!")
                break

            if user_input == "/agents":
                for config in configs:
                    print(f"  {config.display_name} — {config.trigger} — approval: {config.approval}")
                continue

            # Handle approval commands
            if user_input.startswith("approve "):
                action_id = user_input.split(" ", 1)[1].strip()
                if self.approval_manager.approve(action_id):
                    print("\n  ✓ Approved.")
                else:
                    print(f"\n  No pending action: {action_id}")
                continue

            if user_input.startswith("reject "):
                action_id = user_input.split(" ", 1)[1].strip()
                if self.approval_manager.reject(action_id):
                    print("\n  ✗ Rejected.")
                else:
                    print(f"\n  No pending action: {action_id}")
                continue

            # Find which agent to talk to
            target_agent = None
            message = user_input

            for name in self.agents:
                if f"@{name}" in user_input.lower():
                    target_agent = name
                    # Remove the @mention from the message
                    message = user_input.lower().replace(f"@{name}", "").strip()
                    break

            if not target_agent:
                # Check partial matches
                for name in self.agents:
                    parts = name.split()
                    if any(f"@{part}" in user_input.lower() for part in parts):
                        target_agent = name
                        for part in parts:
                            message = message.lower().replace(f"@{part}", "").strip()
                        break

            if not target_agent:
                print("\n  Mention an agent with @ to talk to them.")
                print("  Example: @sales helper help me draft an email")
                continue

            config = self.agents[target_agent]["config"]
            print(f"\n  {config.display_name} is typing...")

            try:
                reply = self.respond(target_agent, message)
                print(f"\n  {config.display_name} >\n")
                # Indent the reply
                for line in reply.split("\n"):
                    print(f"    {line}")
            except Exception as e:
                print(f"\n  Error: {e}")


if __name__ == "__main__":
    agents_dir = os.path.join(os.path.dirname(__file__), "agents")
    chat = DemoChat(agents_dir)
    chat.run()
