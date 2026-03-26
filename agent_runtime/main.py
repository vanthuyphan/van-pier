"""Vanpier - Agent runtime."""

import asyncio
import os
import sys
import time
from pathlib import Path

from nio import (
    AsyncClient,
    LoginResponse,
    RoomMessageText,
    InviteMemberEvent,
)

from .md_parser import load_all_agents, AgentConfig
from .agent import Agent
from .approval import ApprovalManager
from .audit import AuditLog
from .dashboard_api import DashboardAPI

HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://localhost:8008")
AGENTS_DIR = os.environ.get("AGENTS_DIR", "./agents")


class BYOARuntime:
    def __init__(self, homeserver: str, agents_dir: str):
        self.homeserver = homeserver
        self.agents_dir = agents_dir
        self.approval_manager = ApprovalManager()
        self.audit = AuditLog()
        self.bots: dict[str, dict] = {}  # username -> {client, agent}

    async def start(self):
        print("Vanpier starting...")
        print(f"Homeserver: {self.homeserver}")
        print(f"Agents dir: {self.agents_dir}")
        print()

        # Load agent definitions
        print("Loading agents:")
        configs = load_all_agents(self.agents_dir)
        if not configs:
            print("No agents found. Add .md files to the agents/ directory.")
            return

        print(f"\nRegistering {len(configs)} agents with Matrix...\n")

        # Register and start agents sequentially to avoid rate limits
        for config in configs:
            await self._start_agent(config)
            await asyncio.sleep(1)  # Small delay between registrations

        # Start dashboard API
        dashboard = DashboardAPI(self.audit, self)
        await dashboard.start(port=3001)

        # Start the dispatcher — watches all rooms via admin and auto-invites agents
        await self._start_dispatcher()

        # Keep running
        print("\nAll agents online. Listening for messages...\n")
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await self._shutdown()

    async def _start_dispatcher(self):
        """Login as admin to watch all rooms and auto-invite agents on @mention."""
        self.admin_client = AsyncClient(self.homeserver, f"@admin:byoa.local")
        resp = await self.admin_client.login("admin123")
        if not isinstance(resp, LoginResponse):
            print(f"  Dispatcher: failed to login as admin: {resp}")
            return

        print("  Dispatcher online (watches rooms for @mentions)")

        self.admin_client.add_event_callback(
            lambda room, event: self._handle_dispatch(room, event),
            RoomMessageText,
        )

        asyncio.create_task(self._sync_agent(self.admin_client))

    async def _handle_dispatch(self, room, event):
        """When admin sees a message, check for @mentions and auto-invite agents."""
        # Skip messages from agents (not from admin — admin is the human)
        if any(event.sender.startswith(f"@{u}:") for u in self.bots):
            return

        # Ignore old messages
        now_ms = int(time.time() * 1000)
        if now_ms - event.server_timestamp > 30000:
            return

        message = event.body.lower()

        for username, bot in self.bots.items():
            agent = bot["agent"]
            client = bot["client"]

            if agent.should_respond(message):
                # Check if agent is already in the room
                if room.room_id not in client.rooms:
                    # Invite the agent
                    print(f"  Auto-inviting @{username}:byoa.local to {room.room_id}")
                    await self.admin_client.room_invite(
                        room.room_id,
                        f"@{username}:byoa.local",
                    )
                    # Wait for join, then replay the message
                    await asyncio.sleep(3)
                    sender_name = event.sender.split(":")[0].lstrip("@")
                    try:
                        replies = await agent.respond(room.room_id, sender_name, event.body)
                        for reply in replies:
                            await client.room_send(
                                room.room_id,
                                "m.room.message",
                                {"msgtype": "m.text", "body": reply},
                            )
                    except Exception as e:
                        print(f"  Error replaying to {agent.config.name}: {e}")

    async def _start_agent(self, config: AgentConfig):
        username = config.username
        password = f"agent-{username}-pass"

        # Register user first (idempotent)
        await self._register_user(username, password, config.display_name)

        # Now login
        client = AsyncClient(self.homeserver, f"@{username}:byoa.local")
        resp = await client.login(password)
        if not isinstance(resp, LoginResponse):
            print(f"  Failed to login as {username}: {resp}")
            await client.close()
            return

        print(f"  {config.display_name} is online (@{username}:byoa.local)")

        # Set display name
        await client.set_displayname(config.display_name)

        agent = Agent(config, self.approval_manager)
        self.bots[username] = {"client": client, "agent": agent}

        # Handle messages
        client.add_event_callback(
            lambda room, event, bu=username: self._handle_message(bu, room, event),
            RoomMessageText,
        )

        # Auto-accept invites
        client.add_event_callback(
            lambda room, event, c=client: self._handle_invite(c, room, event),
            InviteMemberEvent,
        )

        # Start syncing (skip old messages)
        asyncio.create_task(self._sync_agent(client))

    async def _sync_agent(self, client: AsyncClient):
        """Start syncing, ignoring initial batch of old messages."""
        try:
            resp = await client.sync(timeout=10000, full_state=True)
            print(f"    Sync started for {client.user_id}")
            await client.sync_forever(timeout=30000, since=resp.next_batch)
        except Exception as e:
            print(f"    Sync error for {client.user_id}: {e}")
            import traceback
            traceback.print_exc()

    async def _register_user(self, username: str, password: str, display_name: str) -> bool:
        """Register a new user via the client API."""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            # Step 1: Get session/flows
            async with session.post(
                f"{self.homeserver}/_matrix/client/v3/register",
                json={"username": username, "password": password},
            ) as resp:
                if resp.status == 200:
                    print(f"  Registered @{username}:byoa.local")
                    return True
                body = await resp.json()
                session_id = body.get("session")

            if not session_id:
                # User might already exist
                print(f"  @{username}:byoa.local (may already exist)")
                return True

            # Step 2: Complete with dummy auth
            async with session.post(
                f"{self.homeserver}/_matrix/client/v3/register",
                json={
                    "username": username,
                    "password": password,
                    "initial_device_display_name": display_name,
                    "auth": {
                        "type": "m.login.dummy",
                        "session": session_id,
                    },
                },
            ) as resp:
                if resp.status in (200, 201):
                    print(f"  Registered @{username}:byoa.local")
                    return True
                else:
                    body = await resp.json()
                    if body.get("errcode") == "M_USER_IN_USE":
                        print(f"  @{username}:byoa.local (already exists)")
                        return True
                    print(f"  Registration failed for {username}: {body}")
                    return False

    def _handle_message(self, bot_username: str, room, event):
        """Schedule message handling as a task."""
        asyncio.create_task(self._on_message(bot_username, room, event))

    async def _on_message(self, bot_username: str, room, event):
        """Handle incoming messages."""
        bot = self.bots[bot_username]
        agent: Agent = bot["agent"]
        client: AsyncClient = bot["client"]

        # Don't respond to own messages
        if event.sender == client.user_id:
            return

        # Ignore old messages (more than 30 seconds old)
        age = event.server_timestamp
        now_ms = int(time.time() * 1000)
        if now_ms - age > 30000:
            return

        message = event.body
        print(f"  [{agent.config.name}] Message from {event.sender}: {message}")

        # Handle approval commands globally
        if message.startswith("approve "):
            action_id = message.split(" ", 1)[1].strip()
            if self.approval_manager.approve(action_id):
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": "Approved."},
                )
            return

        if message.startswith("reject "):
            action_id = message.split(" ", 1)[1].strip()
            if self.approval_manager.reject(action_id):
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": "Rejected."},
                )
            return

        # Handle "send" command — execute pending email
        if message.strip().lower() == "send":
            try:
                result = await agent.handle_send_command(room.room_id)
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": result},
                )
            except Exception as e:
                print(f"  Error executing send in {agent.config.name}: {e}")
            return

        # Check if agent should respond
        # In DMs (2 members: agent + human), always respond
        is_dm = len(room.users) <= 2
        if not is_dm and not agent.should_respond(message):
            # Still observe the message for context (memory)
            sender_name = event.sender.split(":")[0].lstrip("@")
            await agent.observe(room.room_id, sender_name, message)
            return

        # Check if agent is disabled
        if getattr(agent, '_disabled', False):
            return

        # Generate response
        sender_name = event.sender.split(":")[0].lstrip("@")
        self.audit.log(agent.config.name, "message", room.room_id, sender_name, message[:200])

        try:
            replies = await agent.respond(room.room_id, sender_name, message)

            for reply in replies:
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": reply},
                )
                self.audit.log(agent.config.name, "response", room.room_id, agent.config.name, reply[:200])
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  Error in {agent.config.name}: {e}")
            self.audit.log(agent.config.name, "error", room.room_id, sender_name, str(e))

    def _handle_invite(self, client, room, event):
        """Schedule invite handling as a task."""
        asyncio.create_task(self._on_invite(client, room, event))

    async def _on_invite(self, client: AsyncClient, room, event):
        """Auto-accept room invites."""
        if event.membership == "invite" and event.state_key == client.user_id:
            await client.join(room.room_id)
            print(f"  Joined room {room.room_id}")

    async def _shutdown(self):
        for username, bot in self.bots.items():
            await bot["client"].close()


def _looks_like_action(text: str) -> bool:
    """Heuristic: does the response look like the agent wants to take an action?"""
    action_indicators = [
        "I'll send", "I will send",
        "I'll create", "I will create",
        "I'll update", "I will update",
        "I'll delete", "I will delete",
        "I'll post", "I will post",
        "sending email", "drafting email",
        "here's the draft", "here is the draft",
    ]
    text_lower = text.lower()
    return any(indicator in text_lower for indicator in action_indicators)


async def run():
    runtime = BYOARuntime(HOMESERVER, AGENTS_DIR)
    await runtime.start()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
