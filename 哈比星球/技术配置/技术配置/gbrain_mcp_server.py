#!/usr/bin/env python3
"""
GBrain MCP Server for Hermes.
Provides 夏夏/夏仁/夏审 with experience database access.
Uses its own SQLite database (independently of Hermes' PGlite-based GBrain).

Tool set:
  - search_memories    : search GBrain for relevant past experiences
  - write_memory       : write a new experience to GBrain
  - list_recent        : list recent memory entries
  - list_tags          : list all unique tags in use
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

# ── DB path ────────────────────────────────────────────────────────────
# Uses its own SQLite file — independent of Hermes' PGlite-based GBrain.
# Database lives alongside the technical configuration files.
GBRAIN_DB = Path("E:/哈比星球/技术配置/gbrain_data.db")

server = Server("gbrain")


# ── DB helpers ─────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Open connection to the GBrain SQLite database, creating it if needed."""
    conn = sqlite3.connect(str(GBRAIN_DB))
    conn.row_factory = sqlite3.Row
    # Ensure table exists on first access
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_need TEXT NOT NULL,
            approach TEXT,
            outcome TEXT,
            what_failed TEXT,
            what_worked TEXT,
            tags TEXT,
            project_dir TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _search(query: str, limit: int = 5) -> list[dict]:
    """Search memories by text match across user_need, approach, tags."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        like = f"%{query}%"
        rows = cur.execute(
            """
            SELECT id, user_need, approach, outcome, what_failed, what_worked,
                   tags, project_dir, created_at
            FROM memos
            WHERE user_need LIKE ? OR approach LIKE ? OR tags LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "user_need": r["user_need"],
                "approach": r["approach"],
                "outcome": r["outcome"],
                "what_failed": r["what_failed"],
                "what_worked": r["what_worked"],
                "tags": r["tags"],
                "project_dir": r["project_dir"],
                "created_at": r["created_at"],
            })
        return results
    finally:
        conn.close()


def _write_memo(user_need: str, approach: str = "", outcome: str = "",
                 what_failed: str = "", what_worked: str = "",
                 tags: str = "") -> int:
    """Write a new memo and return its id."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO memos (user_need, approach, outcome, what_failed,
                               what_worked, tags, project_dir)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_need, approach, outcome, what_failed, what_worked,
              tags, str(Path.cwd())))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _list_recent(count: int = 10) -> list[dict]:
    """List recent memos."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, user_need, outcome, tags, created_at FROM memos "
            "ORDER BY created_at DESC LIMIT ?",
            (count,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _list_tags() -> list[str]:
    """List all unique tags."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute("SELECT DISTINCT tags FROM memos WHERE tags != ''").fetchall()
        tag_set = set()
        for r in rows:
            for t in r[0].split(","):
                tag_set.add(t.strip())
        return sorted(t for t in tag_set if t)
    finally:
        conn.close()


# ── Tool definitions ───────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_memories",
            description="Search GBrain for past experiences, lessons, and decisions. "
                        "Use this BEFORE starting any task to avoid repeating past mistakes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords (e.g. 'prefill edit failed', '角色设定')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (1-20)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="write_memory",
            description="Record a new experience or lesson learned to GBrain. "
                        "Call this after solving a tricky problem, making an architectural decision, "
                        "or discovering a user preference.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_need": {
                        "type": "string",
                        "description": "What the user/agent needed (one sentence)",
                    },
                    "approach": {
                        "type": "string",
                        "description": "What was done to address it",
                    },
                    "outcome": {
                        "type": "string",
                        "description": "Final result: 完成 / 部分完成 / 失败 / etc.",
                    },
                    "what_failed": {
                        "type": "string",
                        "description": "Dead ends, things that didn't work (or 'none')",
                    },
                    "what_worked": {
                        "type": "string",
                        "description": "Key actions that led to success (or 'none')",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags (e.g. 'hermes,config,debug,prefill')",
                    },
                },
                "required": ["user_need", "approach", "outcome"],
            },
        ),
        types.Tool(
            name="list_recent_memories",
            description="List the most recent GBrain entries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of entries to show (1-30)",
                        "default": 10,
                    },
                },
            },
        ),
        types.Tool(
            name="list_tags",
            description="List all unique tags in GBrain. Useful for discovering "
                        "what kind of experiences have been recorded.",
            inputSchema={
                "type": "object",
                "properties": {},
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
        if name == "search_memories":
            results = _search(arguments["query"], arguments.get("limit", 5))
            if not results:
                return [types.TextContent(type="text", text="GBrain 中未找到相关记录。")]
            lines = [f"## GBrain 搜索结果: {arguments['query']}\n"]
            for r in results:
                tags = r.get("tags", "") or ""
                tag_str = f" [{tags}]" if tags else ""
                lines.append(f"### [{r['id']}] {r['user_need']}{tag_str}")
                if r.get("approach"):
                    lines.append(f"- 方法: {r['approach']}")
                if r.get("outcome"):
                    lines.append(f"- 结果: {r['outcome']}")
                if r.get("what_failed") and r["what_failed"] != "none":
                    lines.append(f"- ❌ 失败: {r['what_failed']}")
                if r.get("what_worked") and r["what_worked"] != "none":
                    lines.append(f"- ✅ 成功: {r['what_worked']}")
                lines.append(f"- 时间: {r.get('created_at', '?')}")
                lines.append("")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "write_memory":
            mid = _write_memo(
                user_need=arguments["user_need"],
                approach=arguments.get("approach", ""),
                outcome=arguments.get("outcome", ""),
                what_failed=arguments.get("what_failed", ""),
                what_worked=arguments.get("what_worked", ""),
                tags=arguments.get("tags", ""),
            )
            return [types.TextContent(type="text", text=f"✅ GBrain 已记录 (ID: {mid})")]

        elif name == "list_recent_memories":
            results = _list_recent(arguments.get("count", 10))
            if not results:
                return [types.TextContent(type="text", text="GBrain 暂无记录。")]
            lines = ["## GBrain 最近记录\n"]
            for r in results:
                tags = r.get("tags", "") or ""
                tag_str = f" [{tags}]" if tags else ""
                lines.append(f"- [{r['id']}] {r['user_need']} → {r['outcome']}{tag_str}  ({r.get('created_at', '?')})")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "list_tags":
            tags = _list_tags()
            if not tags:
                return [types.TextContent(type="text", text="GBrain 中暂无标签。")]
            return [types.TextContent(type="text", text="## GBrain 标签\n\n" + "\n".join(f"- {t}" for t in tags))]

        else:
            raise ValueError(f"未知工具: {name}")
    except Exception as e:
        return [types.TextContent(type="text", text=f"GBrain 错误: {str(e)}")]


# ── Entry point ────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="gbrain",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
