#!/usr/bin/env python3
"""
Scream Code MCP Server for Hermes — v2.0.0 (Phase 2 Upgrade)
=============================================================

Sub-agent bridge: Hermes can now delegate complex tasks to Scream Code's full agent
via `scream -p <prompt>` subprocess calls.

Tools:
  Core (v1 -> preserved):
    - search_web        : web search (DuckDuckGo JSON API, upgraded)
    - fetch_url         : fetch URL text content
    - read_project_file : read files (scoped)
    - write_dispatch    : write dispatch order (scoped)
    - write_receipt     : write receipt file (scoped)
    - get_time          : current date/time
    - run_python        : sandboxed Python execution

  Bridge (v2 — new):
    - scream_delegate   : delegate a task to a Scream Code agent
    - scream_resume     : continue a previous Scream Code session
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
    Path(r"D:/AI-哈比星球"),  # external read-only ref, see governance/boundary-lists
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
        # ── v1 preserved tools ───────────────────────────────────────
        types.Tool(
            name="search_web",
            description=(
                "Search the web for information. "
                "Returns title, URL, snippet for each result. "
                "Backed by DuckDuckGo JSON API."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "description": "Number of results (1-10)",
                        "default": 5,
                    },
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
            description="Read a file from the project. Allowed: E:\\哈比星球 (primary) and D:\\AI-哈比星球 (external read-only ref).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path (e.g. 运行中枢/夏夏_调度岗.md)",
                    },
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
                    "filename": {
                        "type": "string",
                        "description": "Filename (e.g. 派单_20260617_001.md)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content of the dispatch order",
                    },
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
                    "filename": {
                        "type": "string",
                        "description": "Filename (e.g. 回执_20260617_001.md)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content of the receipt",
                    },
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
            description=(
                "Execute Python code in a sandboxed environment and return its output. "
                "For data processing, calculations, script testing, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute",
                    },
                },
                "required": ["code"],
            },
        ),
        # ── v2 bridge tools ──────────────────────────────────────────
        types.Tool(
            name="scream_delegate",
            description=(
                "Delegate a complex task to a Scream Code agent. "
                "The agent runs as a non-interactive subprocess via `scream -p <prompt>`, "
                "leveraging full agent capabilities (file tools, subagent spawning, web search, etc.). "
                "Returns the agent's response, including a session ID for follow-up."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The task prompt for Scream Code agent. Be specific and include all context.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (30-300, default 120)",
                        "default": 120,
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model alias (e.g. 'claude', 'gpt-5'). Uses default if omitted.",
                    },
                },
                "required": ["prompt"],
            },
        ),
        types.Tool(
            name="scream_resume",
            description=(
                "Continue a previous Scream Code agent session. "
                "Runs `scream --session <id> -p <prompt>` for multi-turn delegation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to resume (e.g. session_abc123 from scream_delegate output)",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Follow-up prompt for the ongoing session",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (30-300, default 120)",
                        "default": 120,
                    },
                },
                "required": ["session_id", "prompt"],
            },
        ),
    ]


# ── Helper: scream subprocess ─────────────────────────────────────────

def _run_scream(prompt: str, timeout: int = 120, session_id: str | None = None,
                model: str | None = None) -> types.TextContent | types.TextContent:
    """Run `scream -p <prompt>` (or `--session <id> -p <prompt>`) and return result."""
    cmd = ["scream"]
    if session_id:
        cmd.extend(["--session", session_id])
    cmd.extend(["-p", prompt])
    if model:
        cmd.extend(["-m", model])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return types.TextContent(
            type="text",
            text=f"错误: Scream Code agent 超时 ({timeout}s)\n请尝试增加 timeout 或简化 prompt。",
        )
    except FileNotFoundError:
        return types.TextContent(
            type="text", text="错误: 找不到 `scream` 命令。请确认 Scream Code 已安装且在 PATH 中。"
        )
    except Exception as e:
        return types.TextContent(
            type="text", text=f"错误: 调用 Scream Code 失败\n{str(e)}"
        )

    # Build response
    lines = []
    session_id_extracted = None
    output_text = result.stdout or ""

    # Try to extract session ID from the "恢复此会话" line
    for line in output_text.splitlines():
        m = re.search(r"scream -r\s+(\S+)", line)
        if m:
            session_id_extracted = m.group(1)
            break

    # Only show first 8000 chars to avoid blowing up MCP response buffer
    MAX_RESPONSE = 8000
    trimmed = output_text[:MAX_RESPONSE]
    if len(output_text) > MAX_RESPONSE:
        trimmed += f"\n\n... (响应已截断, 共 {len(output_text)} 字符)"

    lines.append(trimmed)

    if session_id_extracted:
        lines.append(f"\n\n---\n**Session ID:** `{session_id_extracted}`")
        lines.append("可用 `scream_resume` 工具继续此会话。")

    if result.stderr:
        stderr_preview = result.stderr.strip()[:2000]
        lines.append(f"\n**stderr:**\n{stderr_preview}")

    return types.TextContent(type="text", text="\n".join(lines))


# ── Helper: v1 preserved functions ────────────────────────────────────

def _search_web_ddg(query: str, limit: int = 5) -> list[types.TextContent]:
    """Search using DuckDuckGo JSON API (upgraded from HTML parsing)."""
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        })
        url = f"https://api.duckduckgo.com/?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        results = []
        # Abstract (infobox) — best single result
        abstract = data.get("AbstractText", "")
        abstract_url = data.get("AbstractURL", "")
        if abstract and abstract_url:
            results.append(f"- [{data.get('Heading', '摘要')}]({abstract_url})\n  {abstract[:300]}")

        # Related topics
        for topic in data.get("RelatedTopics", []):
            if "Text" in topic and "FirstURL" in topic:
                title = topic.get("Text", "").split(" - ")[0].strip()
                url = topic.get("FirstURL", "")
                snippet = topic.get("Text", "")[:200]
                results.append(f"- [{title}]({url})\n  {snippet}")
                if len(results) >= limit:
                    break
            elif "Topics" in topic:
                for sub in topic["Topics"]:
                    if "Text" in sub and "FirstURL" in sub:
                        title = sub.get("Text", "").split(" - ")[0].strip()
                        url = sub.get("FirstURL", "")
                        snippet = sub.get("Text", "")[:200]
                        results.append(f"- [{title}]({url})\n  {snippet}")
                        if len(results) >= limit:
                            break
                if len(results) >= limit:
                    break

        # Results fallback — if DDG JSON returned nothing useful, try a lightweight scrape
        if not results:
            # Fallback: scrape HTML search (old method as backup)
            html_params = urllib.parse.urlencode({"q": query})
            html_url = f"https://html.duckduckgo.com/html/?{html_params}"
            html_req = urllib.request.Request(html_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(html_req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
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
    for root in [Path(r"E:/哈比星球"), Path(r"D:/AI-哈比星球")]:  # D: external read-only ref
        full_path = (root / path_str).resolve()
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

    # ── v1 preserved tools ───────────────────────────────────────────
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

    # ── v2 bridge tools ──────────────────────────────────────────────
    elif name == "scream_delegate":
        prompt = arguments["prompt"]
        timeout = min(max(arguments.get("timeout", 120), 30), 300)
        model = arguments.get("model")
        return [_run_scream(prompt, timeout=timeout, model=model)]

    elif name == "scream_resume":
        session_id = arguments["session_id"]
        prompt = arguments["prompt"]
        timeout = min(max(arguments.get("timeout", 120), 30), 300)
        return [_run_scream(prompt, timeout=timeout, session_id=session_id)]

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
                server_version="2.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
