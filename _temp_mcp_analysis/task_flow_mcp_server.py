#!/usr/bin/env python3
"""
Task Flow MCP Server for Hermes — 只读桥接层（P0 定版后降级）.

P0 定版结论（2026-06-20）：
  - 主系统切换为 Hermes 内建 Kanban
  - task_flow MCP 降级为只读桥接/观察层
  - 写接口（create_task / update_task_status / handoff_task / add_task_log）已关闭

保留只读接口：
  - list_tasks           : list tasks with filters
  - get_task             : get single task detail
  - get_task_stats       : summary statistics

历史数据保留在 task_flow.db 中，供审计查询。
"""

import asyncio
import json
import os
import sqlite3
import datetime
from pathlib import Path
from typing import Any

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

# ── Database ────────────────────────────────────────────────────────────
DB_DIR = Path("E:/哈比星球/技术配置")
DB_PATH = DB_DIR / "task_flow.db"


def _ensure_db():
    """Create tables if they do not exist."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            assignee TEXT DEFAULT '',
            execution_mode TEXT DEFAULT '',
            depends_on TEXT DEFAULT '[]',
            required_inputs TEXT DEFAULT '[]',
            expected_outputs TEXT DEFAULT '[]',
            blocked_policy TEXT DEFAULT 'block_other',
            acceptance_criteria TEXT DEFAULT '[]',
            input_files TEXT DEFAULT '[]',
            output_files TEXT DEFAULT '[]',
            handoff_from TEXT DEFAULT '',
            handoff_to TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL REFERENCES tasks(id),
            action TEXT NOT NULL,
            from_status TEXT DEFAULT '',
            to_status TEXT DEFAULT '',
            role TEXT DEFAULT '',
            note TEXT DEFAULT '',
            timestamp TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
        CREATE INDEX IF NOT EXISTS idx_logs_task ON task_logs(task_id);
    """)
    conn.commit()
    conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────

def _next_task_id() -> str:
    """Generate TASK-YYYYMMDD-NNN with auto-increment per day."""
    today = datetime.date.today().strftime("%Y%m%d")
    prefix = f"TASK-{today}-"
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        "SELECT id FROM tasks WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        last_num = int(row[0].split("-")[-1])
        new_num = last_num + 1
    else:
        new_num = 1
    return f"{prefix}{new_num:03d}"


def _now() -> str:
    return datetime.datetime.now().isoformat()


VALID_STATUSES = ["pending", "assigned", "in_review", "returned", "completed", "archived"]

# Allowed transitions: {from: [to, ...]}
TRANSITIONS = {
    "pending":   ["assigned"],
    "assigned":  ["in_review", "pending"],
    "in_review": ["completed", "returned"],
    "returned":  ["assigned"],
    "completed": ["archived"],
    "archived":  [],
}

ROLES = ["夏夏", "夏仁", "夏审"]

server = Server("task-flow")


def _dict_factory(cursor, row):
    """sqlite3 row -> dict."""
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


# ── Tool definitions ────────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_tasks",
            description="List tasks filtered by status, assignee, or date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by status"},
                    "assignee": {"type": "string", "description": "Filter by assignee role"},
                    "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                    "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
                    "tags": {"type": "string", "description": "Filter by tag keyword"},
                },
            },
        ),
        types.Tool(
            name="get_task",
            description="Get full task details and logs by task ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "TASK-YYYYMMDD-NNN"},
                },
                "required": ["task_id"],
            },
        ),
        types.Tool(
            name="get_task_stats",
            description="Get summary statistics of the task system.",
            inputSchema={
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                },
            },
        ),
    ]


# ── Tool execution ──────────────────────────────────────────────────────

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    if arguments is None:
        arguments = {}

    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = _dict_factory

    try:
        if name == "list_tasks":
            return _do_list(conn, arguments)
        elif name == "get_task":
            return _do_get(conn, arguments)
        elif name == "get_task_stats":
            return _do_stats(conn, arguments)
        else:
            raise ValueError(f"Unknown or disabled tool: {name}")
    finally:
        conn.close()


# ── Tool implementations ────────────────────────────────────────────────

def _do_list(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    where_clauses = []
    params = []

    status = args.get("status")
    if status:
        where_clauses.append("status = ?")
        params.append(status)

    assignee = args.get("assignee")
    if assignee:
        where_clauses.append("assignee = ?")
        params.append(assignee)

    date_from = args.get("date_from")
    if date_from:
        where_clauses.append("created_at >= ?")
        params.append(date_from)

    date_to = args.get("date_to")
    if date_to:
        where_clauses.append("created_at <= ?")
        params.append(date_to + "T23:59:59")

    tags = args.get("tags")
    if tags:
        where_clauses.append("tags LIKE ?")
        params.append(f"%{tags}%")

    limit = min(args.get("limit", 50), 200)
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    query = f"SELECT id, title, status, assignee, execution_mode, created_at, updated_at FROM tasks WHERE {where_sql} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [types.TextContent(type="text", text=json.dumps(rows, ensure_ascii=False))]


def _do_get(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    tid = args["task_id"]
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    if not task:
        return [types.TextContent(type="text", text=json.dumps({"error": "Task not found"}, ensure_ascii=False))]

    logs = conn.execute(
        "SELECT * FROM task_logs WHERE task_id = ? ORDER BY timestamp ASC", (tid,)
    ).fetchall()

    # Parse JSON string fields back to arrays
    for field in ["depends_on", "required_inputs", "expected_outputs",
                   "acceptance_criteria", "input_files", "output_files"]:
        if task.get(field):
            try:
                task[field] = json.loads(task[field])
            except (json.JSONDecodeError, TypeError):
                pass

    result = {"task": task, "logs": logs}
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


def _do_stats(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    where = []
    params = []

    date_from = args.get("date_from")
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from)

    date_to = args.get("date_to")
    if date_to:
        where.append("created_at <= ?")
        params.append(date_to + "T23:59:59")

    where_sql = " AND ".join(where) if where else "1=1"

    # Count by status
    by_status = conn.execute(
        f"SELECT status, COUNT(*) as count FROM tasks WHERE {where_sql} GROUP BY status",
        params,
    ).fetchall()

    # Count by assignee
    by_assignee = conn.execute(
        f"SELECT assignee, COUNT(*) as count FROM tasks WHERE {where_sql} GROUP BY assignee",
        params,
    ).fetchall()

    total = conn.execute(f"SELECT COUNT(*) as c FROM tasks WHERE {where_sql}", params).fetchone()

    result = {
        "total": total["c"],
        "by_status": {r["status"]: r["count"] for r in by_status},
        "by_assignee": {r["assignee"] or "unassigned": r["count"] for r in by_assignee},
    }
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


# ── Entry point ─────────────────────────────────────────────────────────

async def main():
    _ensure_db()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="task-flow",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
