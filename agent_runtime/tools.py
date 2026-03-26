"""Tool registry and execution for BYOA agents."""

import json
import smtplib
import os
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass


@dataclass
class ToolResult:
    success: bool
    message: str


TOOL_DEFINITIONS = [
    {
        "name": "send_email",
        "description": "Send an email to a recipient. Requires approval before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body (plain text)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "draft_email",
        "description": "Draft an email and present it for review. Does NOT send it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body (plain text)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "remember",
        "description": "Save a fact, preference, or note to long-term memory. Use this when learning something important about a person, a decision, or a preference that should be remembered across conversations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["person", "preference", "decision", "note"],
                    "description": "Type of memory to save",
                },
                "key": {"type": "string", "description": "Person name or preference key"},
                "value": {"type": "string", "description": "The information to remember"},
            },
            "required": ["type", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Recall everything from long-term memory. Use this at the start of conversations or when you need context about people, preferences, or past decisions.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for information. Returns search results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
]


def execute_tool(tool_name: str, tool_input: dict) -> ToolResult:
    """Execute a tool by name."""
    if tool_name == "send_email":
        return _send_email(tool_input)
    elif tool_name == "draft_email":
        return _draft_email(tool_input)
    elif tool_name == "web_search":
        return _web_search(tool_input)
    else:
        return ToolResult(success=False, message=f"Unknown tool: {tool_name}")


def _draft_email(params: dict) -> ToolResult:
    """Format an email draft for display."""
    return ToolResult(
        success=True,
        message=(
            f"**Email Draft**\n\n"
            f"**To:** {params['to']}\n"
            f"**Subject:** {params['subject']}\n\n"
            f"---\n{params['body']}\n---"
        ),
    )


def _send_email(params: dict) -> ToolResult:
    """Send an email via SMTP."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("EMAIL_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        return ToolResult(
            success=False,
            message="Email not configured. Set SMTP_USER and SMTP_PASS environment variables.",
        )

    try:
        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = params["to"]
        msg["Subject"] = params["subject"]
        msg.attach(MIMEText(params["body"], "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, params["to"], msg.as_string())

        return ToolResult(success=True, message=f"Email sent to {params['to']}")
    except Exception as e:
        return ToolResult(success=False, message=f"Failed to send email: {e}")


def _web_search(params: dict) -> ToolResult:
    """Search the web using DuckDuckGo instant answer API."""
    query = params["query"]
    try:
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query, "format": "json", "no_html": "1", "skip_disambig": "1"
        })
        req = urllib.request.Request(url, headers={"User-Agent": "BYOA/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        results = []
        if data.get("Abstract"):
            results.append(f"**{data.get('Heading', query)}**: {data['Abstract']}")
            if data.get("AbstractURL"):
                results.append(f"Source: {data['AbstractURL']}")

        for topic in data.get("RelatedTopics", [])[:5]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append(f"- {topic['Text']}")

        if not results:
            return ToolResult(success=True, message=f"No detailed results found for '{query}'. Try a more specific query.")

        return ToolResult(success=True, message="\n".join(results))
    except Exception as e:
        return ToolResult(success=False, message=f"Search failed: {e}")
