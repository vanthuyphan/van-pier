"""Detect and redact PII in outgoing content."""
import re

name = "pii-redactor"
description = "Scans outgoing content for PII (SSN, credit cards, phone) and redacts it"
hook_type = "pre"
priority = 50

PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "phone": re.compile(r"\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
}

OUTGOING_TOOLS = [
    "send_email", "mcp_gmail_send_email",
    "mcp_slack_post_message",
]

def check(ctx):
    from agent_runtime.hooks import HookResult, Decision

    if ctx.tool_name not in OUTGOING_TOOLS:
        return HookResult(Decision.ALLOW)

    text_fields = ["body", "text", "content", "message"]
    found_pii = []
    modified = dict(ctx.tool_input)

    for field in text_fields:
        if field not in modified:
            continue
        text = str(modified[field])
        for pii_type, pattern in PII_PATTERNS.items():
            if pattern.search(text):
                found_pii.append(pii_type)
                text = pattern.sub(f"[REDACTED_{pii_type.upper()}]", text)
        modified[field] = text

    if found_pii:
        return HookResult(
            Decision.ALLOW,
            f"PII detected ({', '.join(found_pii)}) and redacted.",
            modified_input=modified,
        )

    return HookResult(Decision.ALLOW)
