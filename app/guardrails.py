"""Prompt-injection / unsafe-call guardrail (4.2), wired as before_tool_callback.

Returning a dict from the callback SKIPS the tool and uses the dict as its result.
Returning None lets the call proceed.
"""
from __future__ import annotations

BLOCKED_SNIPPETS = ("import os", "import socket", "subprocess", "open(", "__import__", "requests", "urllib")


def before_tool_guard(tool, args, tool_context):
    if tool.name == "run_python":
        code = args.get("code", "")
        for bad in BLOCKED_SNIPPETS:
            if bad in code:
                return {"error": f"blocked by policy: code contains '{bad}'"}
    return None
