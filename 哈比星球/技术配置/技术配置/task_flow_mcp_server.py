#!/usr/bin/env python3
"""
Task Flow MCP Server for Hermes — 两岗位任务流转引擎.
Implements the task workflow defined in Hermes落地配置方案.md / 夏仁_执行岗.md / 夏审_审核岗.md.

Architecture: 夏夏 (Core Orchestrator) directly assigns tasks to 夏仁 (Executor) or 夏审 (Reviewer).
No intermediate dispatcher role — 夏夏 is the dispatcher by nature.

Task numbering: TASK-YYYYMMDD-NNN (auto-increment per day)
Workflow states:
  pending -> assigned -> in_review -> completed -> archived
                                   \\-> returned -> assigned

Tool set:
  - create_task          : create a new task with auto-numbering
  - list_tasks           : list tasks with filters
  - get_task             : get single task detail
  - update_task_status   : advance/reject task along workflow
  - handoff_task         : transfer task between roles
  - add_task_log         : append a log entry
  - get_task_stats       : summary statistics
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
            name="create_task",
            description="Create a new task. Auto-generates TASK-YYYYMMDD-NNN id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "description": {"type": "string", "description": "Task description / requirement"},
                    "assignee": {"type": "string", "description": "Initial assignee role: 夏夏 / 夏仁 / 夏审"},
                    "execution_mode": {"type": "string", "description": "Mode for executor: writing / xhs / douyin / automation"},
                    "depends_on": {
                        "type": "array", "items": {"type": "string"},
                        "description": "List of TASK-IDs this task depends on",
                    },
                    "blocked_policy": {
                        "type": "string",
                        "description": "block_other (default) / skip_dependents / force_proceed",
                    },
                    "acceptance_criteria": {
                        "type": "array", "items": {"type": "string"},
                        "description": "List of acceptance criteria",
                    },
                    "input_files": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Reference files / source materials",
                    },
                    "tags": {"type": "string", "description": "Space-separated tags"},
                },
            },
        ),
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
            name="update_task_status",
            description="Transition a task to a new status. Validates allowed transitions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "TASK-YYYYMMDD-NNN"},
                    "new_status": {
                        "type": "string",
                        "enum": VALID_STATUSES,
                        "description": "Target status",
                    },
                    "note": {"type": "string", "description": "Optional reason or comment"},
                    "role": {"type": "string", "description": "Role performing the action"},
                },
                "required": ["task_id", "new_status"],
            },
        ),
        types.Tool(
            name="handoff_task",
            description="Handoff a task to another role. Validates handoff requirements per Hermes architecture.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "TASK-YYYYMMDD-NNN"},
                    "from_role": {"type": "string", "description": "Current role"},
                    "to_role": {"type": "string", "description": "Target role"},
                    "note": {"type": "string", "description": "Handoff message"},
                },
                "required": ["task_id", "from_role", "to_role"],
            },
        ),
        types.Tool(
            name="add_task_log",
            description="Append an arbitrary log entry to a task (e.g. for intermediate progress).",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "TASK-YYYYMMDD-NNN"},
                    "action": {"type": "string", "description": "Action label (e.g. 'progress','note','checkpoint')"},
                    "note": {"type": "string", "description": "Log content"},
                    "role": {"type": "string", "description": "Role adding the note"},
                },
                "required": ["task_id", "action", "note"],
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
        if name == "create_task":
            return _do_create(conn, arguments)
        elif name == "list_tasks":
            return _do_list(conn, arguments)
        elif name == "get_task":
            return _do_get(conn, arguments)
        elif name == "update_task_status":
            return _do_update_status(conn, arguments)
        elif name == "handoff_task":
            return _do_handoff(conn, arguments)
        elif name == "add_task_log":
            return _do_add_log(conn, arguments)
        elif name == "get_task_stats":
            return _do_stats(conn, arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    finally:
        conn.close()


# ── Tool implementations ────────────────────────────────────────────────

def _do_create(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    tid = _next_task_id()
    now = _now()
    title = args.get("title", "Untitled Task")
    status = "pending"
    assignee = args.get("assignee", "")
    execution_mode = args.get("execution_mode", "")
    depends_on = json.dumps(args.get("depends_on", []))
    blocked_policy = args.get("blocked_policy", "block_other")
    acceptance_criteria = json.dumps(args.get("acceptance_criteria", []))
    input_files = json.dumps(args.get("input_files", []))
    tags = args.get("tags", "")

    conn.execute(
        """INSERT INTO tasks
           (id, title, description, status, assignee, execution_mode,
            depends_on, blocked_policy, acceptance_criteria, input_files,
            tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tid, title, args.get("description", ""), status, assignee,
         execution_mode, depends_on, blocked_policy, acceptance_criteria,
         input_files, tags, now, now),
    )
    conn.execute(
        "INSERT INTO task_logs (task_id, action, from_status, to_status, role, note, timestamp) VALUES (?,?,?,?,?,?,?)",
        (tid, "created", "", status, assignee or "system", "Task created", now),
    )
    conn.commit()
    return [types.TextContent(type="text", text=json.dumps({
        "task_id": tid, "status": status, "created_at": now
    }, ensure_ascii=False))]


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


def _do_update_status(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    tid = args["task_id"]
    new_status = args["new_status"]
    note = args.get("note", "")
    role = args.get("role", "")

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    if not task:
        return [types.TextContent(type="text", text=json.dumps({"error": "Task not found"}, ensure_ascii=False))]

    old_status = task["status"]
    allowed = TRANSITIONS.get(old_status, [])

    if new_status not in allowed:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Cannot transition from '{old_status}' to '{new_status}'. Allowed: {allowed}"
        }, ensure_ascii=False))]

    now = _now()
    conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                 (new_status, now, tid))
    conn.execute(
        "INSERT INTO task_logs (task_id, action, from_status, to_status, role, note, timestamp) VALUES (?,?,?,?,?,?,?)",
        (tid, "status_change", old_status, new_status, role, note, now),
    )
    conn.commit()

    # If dependencies were blocked, check them
    if new_status == "completed":
        _check_dependents(conn, tid)

    return [types.TextContent(type="text", text=json.dumps({
        "task_id": tid, "from": old_status, "to": new_status, "timestamp": now
    }, ensure_ascii=False))]


def _do_handoff(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    tid = args["task_id"]
    from_role = args["from_role"]
    to_role = args["to_role"]
    note = args.get("note", "")

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    if not task:
        return [types.TextContent(type="text", text=json.dumps({"error": "Task not found"}, ensure_ascii=False))]

    # Handoff checks per Hermes architecture:
    # 1. If depends_on exist, verify they are complete
    if task.get("depends_on"):
        try:
            deps = json.loads(task["depends_on"])
            for dep_id in deps:
                dep = conn.execute("SELECT status FROM tasks WHERE id = ?", (dep_id,)).fetchone()
                if dep and dep["status"] not in ("completed", "archived", "skipped"):
                    return [types.TextContent(type="text", text=json.dumps({
                        "error": f"Dependency {dep_id} has status '{dep['status']}', not complete"
                    }, ensure_ascii=False))]
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. Check blocked policy
    if task.get("status") == "blocked" and task.get("blocked_policy") == "block_other":
        return [types.TextContent(type="text", text=json.dumps({
            "error": "Task is blocked with policy 'block_other'. Resolve blockage first."
        }, ensure_ascii=False))]

    now = _now()
    conn.execute(
        "UPDATE tasks SET assignee = ?, handoff_from = ?, handoff_to = ?, updated_at = ? WHERE id = ?",
        (to_role, from_role, to_role, now, tid),
    )
    conn.execute(
        "INSERT INTO task_logs (task_id, action, from_status, to_status, role, note, timestamp) VALUES (?,?,?,?,?,?,?)",
        (tid, "handoff", task["status"], task["status"], f"{from_role}->{to_role}", note, now),
    )
    conn.commit()

    return [types.TextContent(type="text", text=json.dumps({
        "task_id": tid, "from_role": from_role, "to_role": to_role, "timestamp": now
    }, ensure_ascii=False))]


def _do_add_log(conn: sqlite3.Connection, args: dict) -> list[types.TextContent]:
    tid = args["task_id"]
    action = args["action"]
    note = args.get("note", "")
    role = args.get("role", "")
    now = _now()

    task = conn.execute("SELECT id FROM tasks WHERE id = ?", (tid,)).fetchone()
    if not task:
        return [types.TextContent(type="text", text=json.dumps({"error": "Task not found"}, ensure_ascii=False))]

    conn.execute(
        "INSERT INTO task_logs (task_id, action, role, note, timestamp) VALUES (?,?,?,?,?)",
        (tid, action, role, note, now),
    )
    conn.commit()
    return [types.TextContent(type="text", text=json.dumps({"task_id": tid, "action": action, "timestamp": now}, ensure_ascii=False))]


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


def _check_dependents(conn: sqlite3.Connection, completed_tid: str):
    """When a task completes, check if any task depends on it and log a note."""
    deps = conn.execute(
        "SELECT id, title FROM tasks WHERE depends_on LIKE ? AND status = 'pending'",
        (f"%{completed_tid}%",),
    ).fetchall()
    for d in deps:
        conn.execute(
            "INSERT INTO task_logs (task_id, action, note, timestamp) VALUES (?,?,?,?)",
            (d["id"], "dependency_met",
             f"Dependency {completed_tid} completed — task ready for assignment",
             _now()),
        )
    conn.commit()


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
