#!/usr/bin/env python3
"""
Dreamina MCP Server for Hermes.
Wraps Dreamina CLI (v1.4.3) as MCP tools for 夏夏/夏仁.

Tool set:
  - generate_image    : generate image via Dreamina
  - check_task        : check task status
  - list_tasks        : list recent Dreamina tasks
  - get_result        : get generated image URL
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

# ── Paths ──────────────────────────────────────────────────────────────
DREAMINA_HOME = Path.home() / ".dreamina_cli"
DREAMINA_BIN = Path.home() / "bin" / "dreamina"
DREAMINA_DB = DREAMINA_HOME / "tasks.db"

server = Server("dreamina")


# ── CLI helpers ────────────────────────────────────────────────────────

def _run_dreamina(*args: str, timeout: int = 120) -> str:
    """Run dreamina CLI and return stdout."""
    cmd = [str(DREAMINA_BIN)] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "DREAMINA_CLI_HOME": str(DREAMINA_HOME)},
        )
        if result.returncode != 0:
            raise RuntimeError(f"Dreamina CLI 失败 (code {result.returncode}): {result.stderr[:500]}")
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError(f"Dreamina CLI 未找到: {DREAMINA_BIN}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Dreamina CLI 超时 ({timeout}s)")


def _check_dreamina_installed() -> bool:
    """Check if dreamina CLI is available."""
    return DREAMINA_BIN.exists() and os.access(str(DREAMINA_BIN), os.X_OK)


def _list_tasks_from_db(limit: int = 10) -> list[dict]:
    """Read recent tasks from tasks.db (SQLite)."""
    if not DREAMINA_DB.exists():
        return []
    import sqlite3
    conn = sqlite3.connect(f"file:{DREAMINA_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, prompt, status, created_at, result_url "
            "FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Tool definitions ───────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="generate_image",
            description="Generate an image using Dreamina AI. "
                        "Provide a detailed prompt in Chinese describing the desired image.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Image description in Chinese (detailed, specific)",
                    },
                    "style": {
                        "type": "string",
                        "description": "Style: 写实/动漫/插画/3D/水彩/水墨/油画/概念设计",
                        "default": "动漫",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Aspect ratio: 1:1 / 16:9 / 9:16 / 4:3 / 3:4",
                        "default": "1:1",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of images to generate (1-4)",
                        "default": 1,
                    },
                },
                "required": ["prompt"],
            },
        ),
        types.Tool(
            name="check_task",
            description="Check the status of a Dreamina task by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID to check",
                    },
                },
                "required": ["task_id"],
            },
        ),
        types.Tool(
            name="list_tasks",
            description="List recent Dreamina generation tasks and their status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of tasks to show (1-20)",
                        "default": 10,
                    },
                },
            },
        ),
    ]


# ── Tool execution ─────────────────────────────────────────────────────

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    if arguments is None:
        arguments = {}

    try:
        if not _check_dreamina_installed():
            return [types.TextContent(
                type="text",
                text="⚠️ Dreamina CLI 未安装或不可执行。请运行: hermes skill install dreamina"
            )]

        if name == "generate_image":
            prompt = arguments["prompt"]
            style = arguments.get("style", "动漫")
            ratio = arguments.get("aspect_ratio", "1:1")
            count = str(arguments.get("count", 1))

            output = _run_dreamina(
                "generate",
                "--prompt", prompt,
                "--style", style,
                "--aspect-ratio", ratio,
                "--count", count,
            )
            return [types.TextContent(type="text", text=output)]

        elif name == "check_task":
            task_id = arguments["task_id"]
            output = _run_dreamina("status", task_id)
            return [types.TextContent(type="text", text=output)]

        elif name == "list_tasks":
            limit = arguments.get("limit", 10)
            # Try DB first, then CLI
            tasks = _list_tasks_from_db(limit)
            if tasks:
                lines = ["## Dreamina 最近任务\n"]
                for t in tasks:
                    url = t.get("result_url", "") or ""
                    url_str = f" → {url}" if url else ""
                    lines.append(f"- [{t['id']}] {t.get('prompt', '?')[:50]}... "
                                 f"[{t.get('status', '?')}]{url_str}  ({t.get('created_at', '?')})")
                return [types.TextContent(type="text", text="\n".join(lines))]
            # Fallback to CLI
            output = _run_dreamina("list", "--limit", str(limit))
            return [types.TextContent(type="text", text=output)]

        else:
            raise ValueError(f"未知工具: {name}")
    except Exception as e:
        return [types.TextContent(type="text", text=f"Dreamina 错误: {str(e)}")]


# ── Entry point ────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="dreamina",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
