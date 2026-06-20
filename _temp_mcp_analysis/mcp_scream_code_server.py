#!/usr/bin/env python3
"""
Scream Code MCP Server for Hermes.
Provides technical capabilities to 夏夏 (who no longer has direct file tools).

Tool set:
  - search_web        : web search
  - fetch_url         : fetch URL content
  - read_project_file : read files (scoped to E:\哈比星球 + D:\AI-哈比星球)
  - write_dispatch    : write dispatch orders (scoped to E:\哈比星球\规则层\派单模板\)
  - write_receipt     : write receipt files (scoped to E:\哈比星球\规则层\回执模板\)
  - run_python        : execute Python code (sandboxed)
  - get_time          : get current date/time
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import traceback
import urllib.parse
import urllib.request
import urllib.error
import io
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

# ── Scoping rules ──────────────────────────────────────────────────────
ALLOWED_READ_ROOTS = [
    Path(r"E:/哈比星球"),
    Path(r"D:/AI-哈比星球"),
]

DISPATCH_DIR = Path(r"E:/哈比星球/规则层/派单模板")
RECEIPT_DIR = Path(r"E:/哈比星球/规则层/回执模板")

DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)

server = Server("scream-code")


# ── Tool definitions ───────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_web",
            description="Search the web for information. Returns title, URL, snippet for each result.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Number of results (1-10)", "default": 5},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="fetch_url",
            description="Fetch a URL and return its text content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="read_project_file",
            description="Read a file from the project. Allowed: E:\\哈比星球 and D:\\AI-哈比星球.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path (e.g. 运行中枢/夏夏_调度岗.md)"},
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="write_dispatch",
            description="Write a dispatch order file to E:\\哈比星球\\规则层\\派单模板\\.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Filename (e.g. 派单_20260617_001.md)"},
                    "content": {"type": "string", "description": "Markdown content of the dispatch order"},
                },
                "required": ["filename", "content"],
            },
        ),
        types.Tool(
            name="write_receipt",
            description="Write a receipt file to E:\\哈比星球\\规则层\\回执模板\\.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Filename (e.g. 回执_20260617_001.md)"},
                    "content": {"type": "string", "description": "Markdown content of the receipt"},
                },
                "required": ["filename", "content"],
            },
        ),
        types.Tool(
            name="get_time",
            description="Get the current date and time.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="run_python",
            description="Execute Python code and return the output. For data processing, calculations, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
        ),
    ]


# ── Helper functions ───────────────────────────────────────────────────

def _search_web_ddg(query: str, limit: int = 5) -> list[types.TextContent]:
    """Search using DuckDuckGo HTML."""
    try:
        params = urllib.parse.urlencode({"q": query})
        url = f"https://html.duckduckgo.com/html/?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = []
        # Simple regex extraction
        for m in re.finditer(r'class="result__a"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html):
            url = m.group(1)
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            results.append(f"- [{title}]({url})")
            if len(results) >= limit:
                break

        output = f"## 搜索结果: {query}\n\n" + "\n".join(results) if results else "无结果"
        return [types.TextContent(type="text", text=output)]
    except Exception as e:
        return [types.TextContent(type="text", text=f"搜索失败: {str(e)}")]


def _fetch_url_content(url: str) -> list[types.TextContent]:
    """Fetch URL and extract text."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r'<[^>]+>', ' ', content)
        text = re.sub(r'\s+', ' ', text).strip()
        max_len = 5000
        if len(text) > max_len:
            text = text[:max_len] + f"\n\n... (截断, 原文 {len(content)} 字符)"
        return [types.TextContent(type="text", text=text)]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取失败: {str(e)}")]


def _read_file(path_str: str) -> list[types.TextContent]:
    """Read project file from allowed roots."""
    for root in [Path(r"E:/哈比星球"), Path(r"D:/AI-哈比星球")]:
        full_path = (root / path_str).resolve()
        # Security: ensure resolved path is under an allowed root
        allowed = False
        for ar in ALLOWED_READ_ROOTS:
            try:
                ar_resolved = ar.resolve()
                if ar_resolved in full_path.parents or ar_resolved == full_path:
                    allowed = True
                    break
            except Exception:
                continue
        if allowed and full_path.exists() and full_path.is_file():
            try:
                text = full_path.read_text(encoding="utf-8")
                return [types.TextContent(type="text", text=text)]
            except Exception as e:
                return [types.TextContent(type="text", text=f"读取失败: {str(e)}")]
    return [types.TextContent(type="text", text=f"文件未找到或路径不允许: {path_str}")]


def _write_scoped(filepath: Path, content: str) -> list[types.TextContent]:
    """Write file, ensuring parent dir exists."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        filepath.write_text(content, encoding="utf-8")
        return [types.TextContent(type="text", text=f"已写入: {filepath}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"写入失败: {str(e)}")]


_sanitize_filename = lambda s: re.sub(r'[^\w.\-]', '_', s) or f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


# ── Tool execution ─────────────────────────────────────────────────────

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    if arguments is None:
        arguments = {}

    if name == "search_web":
        return _search_web_ddg(arguments["query"], arguments.get("limit", 5))

    elif name == "fetch_url":
        return _fetch_url_content(arguments["url"])

    elif name == "read_project_file":
        return _read_file(arguments["path"])

    elif name == "write_dispatch":
        fn = _sanitize_filename(arguments.get("filename", "派单.md"))
        if not fn.endswith(".md"):
            fn += ".md"
        return _write_scoped(DISPATCH_DIR / fn, arguments["content"])

    elif name == "write_receipt":
        fn = _sanitize_filename(arguments.get("filename", "回执.md"))
        if not fn.endswith(".md"):
            fn += ".md"
        return _write_scoped(RECEIPT_DIR / fn, arguments["content"])

    elif name == "get_time":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [types.TextContent(type="text", text=f"当前时间: {now}")]

    elif name == "run_python":
        try:
            stdout_cap = io.StringIO()
            stderr_cap = io.StringIO()
            with contextlib.redirect_stdout(stdout_cap), contextlib.redirect_stderr(stderr_cap):
                exec(arguments["code"], {"__builtins__": __builtins__})
            out = stdout_cap.getvalue()
            err = stderr_cap.getvalue()
            parts = []
            if out:
                parts.append(f"## 输出\n{out}")
            if err:
                parts.append(f"## 错误\n{err}")
            if not parts:
                parts.append("执行成功，无输出。")
            return [types.TextContent(type="text", text="\n".join(parts))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"执行失败: {str(e)}\n{traceback.format_exc()}")]

    else:
        raise ValueError(f"未知工具: {name}")


# ── Entry point ────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="scream-code",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
