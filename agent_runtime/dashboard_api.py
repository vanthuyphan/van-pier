"""Simple HTTP API for the admin dashboard."""

import json
import os
from pathlib import Path
from aiohttp import web
from .audit import AuditLog


class DashboardAPI:
    def __init__(self, audit_log: AuditLog, runtime):
        self.audit = audit_log
        self.runtime = runtime

    async def start(self, port: int = 3001):
        @web.middleware
        async def cors_middleware(request, handler):
            if request.method == 'OPTIONS':
                resp = web.Response(status=200)
            else:
                resp = await handler(request)
            resp.headers['Access-Control-Allow-Origin'] = '*'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return resp

        app = web.Application(middlewares=[cors_middleware])
        app.router.add_get('/api/agents', self.get_agents)
        app.router.add_get('/api/audit', self.get_audit)
        app.router.add_get('/api/stats', self.get_stats)
        app.router.add_post('/api/agents/create', self.create_agent)
        app.router.add_post('/api/agents/{username}/disable', self.disable_agent)
        app.router.add_post('/api/agents/{username}/enable', self.enable_agent)
        app.router.add_get('/api/policy', self.get_policy)
        app.router.add_post('/api/policy', self.save_policy)
        app.router.add_get('/api/tools', self.get_all_tools)
        app.router.add_get('/api/tasks', self.get_tasks)
        app.router.add_post('/api/tasks/create', self.create_task)
        app.router.add_post('/api/tasks/{task_id}/run', self.run_task)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"  Dashboard API on http://localhost:{port}")

    async def get_agents(self, request):
        agents = []
        for username, bot in self.runtime.bots.items():
            agent = bot["agent"]
            agents.append({
                "username": username,
                "name": agent.config.name,
                "avatar": agent.config.avatar,
                "trigger": agent.config.trigger,
                "tools": agent.config.tools,
                "approval": agent.config.approval,
                "enabled": not getattr(agent, '_disabled', False),
                "source_file": agent.config.source_file,
            })
        return web.json_response(agents)

    async def get_audit(self, request):
        limit = int(request.query.get('limit', '100'))
        agent = request.query.get('agent')
        event_type = request.query.get('type')
        entries = self.audit.get_recent(limit=limit, agent_name=agent, event_type=event_type)
        return web.json_response(entries)

    async def get_stats(self, request):
        stats = self.audit.get_agent_stats()
        return web.json_response(stats)

    async def create_agent(self, request):
        """Save a new .md agent file."""
        try:
            data = await request.json()
            filename = data.get('filename', '').strip()
            content = data.get('content', '').strip()

            if not filename or not content:
                return web.json_response({"error": "filename and content required"}, status=400)

            # Sanitize filename
            filename = filename.replace('/', '').replace('..', '')
            if not filename.endswith('.md'):
                filename += '.md'

            agents_dir = self.runtime.agents_dir
            filepath = Path(agents_dir) / filename

            if filepath.exists():
                return web.json_response({"error": f"Agent '{filename}' already exists"}, status=409)

            filepath.write_text(content)
            self.audit.log("system", "agent_created", "", "admin", f"Created agent: {filename}")

            return web.json_response({"status": "ok", "file": str(filepath)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def disable_agent(self, request):
        username = request.match_info['username']
        if username in self.runtime.bots:
            self.runtime.bots[username]["agent"]._disabled = True
            self.audit.log(username, "disabled", "", "admin", "Agent disabled by admin")
            return web.json_response({"status": "disabled"})
        return web.json_response({"error": "not found"}, status=404)

    async def enable_agent(self, request):
        username = request.match_info['username']
        if username in self.runtime.bots:
            self.runtime.bots[username]["agent"]._disabled = False
            self.audit.log(username, "enabled", "", "admin", "Agent enabled by admin")
            return web.json_response({"status": "enabled"})
        return web.json_response({"error": "not found"}, status=404)

    async def get_policy(self, request):
        """Get current policy.yaml."""
        import yaml
        policy_path = Path("./hooks/policy.yaml")
        if policy_path.exists():
            policy = yaml.safe_load(policy_path.read_text()) or {}
        else:
            policy = {}
        return web.json_response(policy)

    async def save_policy(self, request):
        """Save updated policy.yaml."""
        import yaml
        data = await request.json()
        policy_path = Path("./hooks/policy.yaml")
        policy_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        return web.json_response({"status": "saved"})

    async def get_all_tools(self, request):
        """Get all tools across all agents (built-in + MCP)."""
        tools = []
        seen = set()
        for username, bot in self.runtime.bots.items():
            agent = bot["agent"]
            for tool_def in agent._get_tools():
                name = tool_def["name"]
                if name in seen:
                    continue
                seen.add(name)
                tools.append({
                    "name": name,
                    "description": tool_def.get("description", ""),
                    "agents": [username],
                })
        # Add agents to existing tools
        for username, bot in self.runtime.bots.items():
            agent = bot["agent"]
            for tool_def in agent._get_tools():
                for t in tools:
                    if t["name"] == tool_def["name"] and username not in t["agents"]:
                        t["agents"].append(username)
        return web.json_response(tools)

    async def get_tasks(self, request):
        from dataclasses import asdict
        tasks = self.runtime.task_manager.list_tasks()
        return web.json_response([asdict(t) for t in tasks])

    async def create_task(self, request):
        """Plan a new task via the API."""
        try:
            data = await request.json()
            description = data.get("description", "")
            room_id = data.get("room_id", "")

            if not description:
                return web.json_response({"error": "description required"}, status=400)

            task = await self.runtime.task_runner.plan_task(description, room_id, "admin")
            from dataclasses import asdict
            return web.json_response(asdict(task))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def run_task(self, request):
        """Start running a planned task."""
        task_id = request.match_info['task_id']
        task = self.runtime.task_manager.get_task(task_id)
        if not task:
            return web.json_response({"error": "not found"}, status=404)

        # Find an agent client to send messages
        first_bot = next(iter(self.runtime.bots.values()))
        client = first_bot["client"]

        async def send_to_room(rid, text):
            await client.room_send(rid, "m.room.message", {"msgtype": "m.text", "body": text})

        import asyncio
        asyncio.create_task(self.runtime.task_runner.run_task(task_id, send_to_room))
        return web.json_response({"status": "started"})
