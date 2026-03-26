"""MCP client — connects agents to MCP tool servers."""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict
    server_name: str  # which MCP server provides this


@dataclass
class MCPServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    process: asyncio.subprocess.Process | None = None
    tools: list[MCPTool] = field(default_factory=list)
    _request_id: int = 0
    _pending: dict = field(default_factory=dict)
    _reader_task: asyncio.Task | None = None


class MCPClient:
    """Manages MCP server connections for an agent."""

    def __init__(self):
        self.servers: dict[str, MCPServer] = {}

    async def connect(self, name: str, command: str, args: list[str] = None,
                      env: dict[str, str] = None) -> list[MCPTool]:
        """Start an MCP server and discover its tools."""
        server = MCPServer(
            name=name,
            command=command,
            args=args or [],
            env=env or {},
        )

        # Resolve env vars
        resolved_env = dict(os.environ)
        for k, v in server.env.items():
            if v.startswith("${") and v.endswith("}"):
                env_key = v[2:-1]
                resolved_env[k] = os.environ.get(env_key, "")
            else:
                resolved_env[k] = v

        # Build command
        cmd_parts = command.split()
        if args:
            cmd_parts.extend(args)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=resolved_env,
            )
            server.process = process
            print(f"    MCP server '{name}' started (PID {process.pid})")

            # Start reading responses
            server._reader_task = asyncio.create_task(self._read_responses(server))

            # Initialize
            await self._send_request(server, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "vanpier", "version": "1.0"},
            })

            # Send initialized notification
            await self._send_notification(server, "notifications/initialized", {})

            # List tools
            result = await self._send_request(server, "tools/list", {})
            if result and "tools" in result:
                for tool_def in result["tools"]:
                    tool = MCPTool(
                        name=tool_def["name"],
                        description=tool_def.get("description", ""),
                        input_schema=tool_def.get("inputSchema", {"type": "object", "properties": {}}),
                        server_name=name,
                    )
                    server.tools.append(tool)

            self.servers[name] = server
            print(f"    MCP server '{name}': {len(server.tools)} tools available")
            for tool in server.tools:
                print(f"      - {tool.name}: {tool.description[:60]}")

            return server.tools

        except FileNotFoundError:
            print(f"    MCP server '{name}': command not found: {command}")
            return []
        except Exception as e:
            print(f"    MCP server '{name}' failed to start: {e}")
            return []

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Call a tool on an MCP server."""
        server = self.servers.get(server_name)
        if not server or not server.process:
            return f"Error: MCP server '{server_name}' not connected"

        try:
            result = await self._send_request(server, "tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })

            if result and "content" in result:
                parts = []
                for block in result["content"]:
                    if block.get("type") == "text":
                        parts.append(block["text"])
                    elif block.get("type") == "image":
                        parts.append(f"[Image: {block.get('mimeType', 'image')}]")
                    else:
                        parts.append(json.dumps(block))
                return "\n".join(parts)
            elif result and "isError" in result:
                return f"Error: {result.get('content', 'Unknown error')}"
            else:
                return json.dumps(result) if result else "No result"

        except Exception as e:
            return f"Error calling {tool_name}: {e}"

    async def _send_request(self, server: MCPServer, method: str, params: dict) -> dict | None:
        """Send a JSON-RPC request and wait for response."""
        server._request_id += 1
        req_id = server._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future = asyncio.get_event_loop().create_future()
        server._pending[req_id] = future

        line = json.dumps(message) + "\n"
        server.process.stdin.write(line.encode())
        await server.process.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=30)
            return result
        except asyncio.TimeoutError:
            server._pending.pop(req_id, None)
            print(f"    MCP '{server.name}': request timed out: {method}")
            return None

    async def _send_notification(self, server: MCPServer, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(message) + "\n"
        server.process.stdin.write(line.encode())
        await server.process.stdin.drain()

    async def _read_responses(self, server: MCPServer):
        """Read JSON-RPC responses from the MCP server."""
        try:
            while server.process and server.process.returncode is None:
                line = await server.process.stdout.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                if "id" in msg and msg["id"] in server._pending:
                    future = server._pending.pop(msg["id"])
                    if "result" in msg:
                        future.set_result(msg["result"])
                    elif "error" in msg:
                        future.set_result({"isError": True, "content": msg["error"]})
                    else:
                        future.set_result(msg)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"    MCP '{server.name}' reader error: {e}")

    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected servers."""
        tools = []
        for server in self.servers.values():
            tools.extend(server.tools)
        return tools

    def get_tool_definitions(self) -> list[dict]:
        """Get tool definitions in Claude API format."""
        defs = []
        for tool in self.get_all_tools():
            defs.append({
                "name": f"mcp_{tool.server_name}_{tool.name}",
                "description": f"[{tool.server_name}] {tool.description}",
                "input_schema": tool.input_schema,
            })
        return defs

    def find_tool(self, full_name: str) -> tuple[str, str] | None:
        """Parse 'mcp_servername_toolname' back to (server_name, tool_name)."""
        if not full_name.startswith("mcp_"):
            return None
        rest = full_name[4:]
        for server_name in self.servers:
            prefix = f"{server_name}_"
            if rest.startswith(prefix):
                tool_name = rest[len(prefix):]
                return (server_name, tool_name)
        return None

    async def shutdown(self):
        """Stop all MCP servers."""
        for name, server in self.servers.items():
            if server._reader_task:
                server._reader_task.cancel()
            if server.process:
                server.process.terminate()
                await server.process.wait()
                print(f"    MCP server '{name}' stopped")
