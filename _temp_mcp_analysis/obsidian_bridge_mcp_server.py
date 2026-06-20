#!/usr/bin/env python3
"""
Obsidian Bridge MCP Server for Hermes.
Provides 夏夏/夏仁/夏审 with direct read/write access to the Obsidian vault
at E:/哈比星球. Works without Obsidian running — reads Markdown files directly
from the filesystem.

Tool set:
  - read_vault_file    : read a markdown file from the vault
  - write_vault_file   : write/update a markdown file in the vault
  - list_vault_files   : list files in a vault directory
  - search_vault       : search vault files by content or filename
  - read_frontmatter   : extract YAML frontmatter from a markdown file
  - get_vault_tree     : get the top-level vault directory structure
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

# ── Vault path ──────────────────────────────────────────────────────────
VAULT_PATH = Path("E:/哈比星球")

server = Server("obsidian-bridge")


# ── Helpers ─────────────────────────────────────────────────────────────

def _resolve_path(relative: str) -> Path:
    """Resolve a vault-relative path, preventing directory traversal."""
    p = (VAULT_PATH / relative).resolve()
    if not str(p).startswith(str(VAULT_PATH.resolve())):
        raise ValueError(f"路径越界: {relative}")
    return p


def _list_files(dir_path: Path, extension: str = ".md") -> list[dict]:
    """List markdown files in a directory (non-recursive)."""
    results = []
    if not dir_path.is_dir():
        return results
    for entry in sorted(dir_path.iterdir()):
        if entry.is_file() and entry.suffix == extension:
            stat = entry.stat()
            results.append({
                "name": entry.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
    return results


def _read_markdown(file_path: Path) -> str:
    """Read a markdown file, returning its content."""
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    return file_path.read_text(encoding="utf-8")


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content as a dict."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", content, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from markdown content."""
    return re.sub(r"^---\s*\n.*?\n---\s*\n?", "", content, count=1, flags=re.DOTALL)


# ── Tool definitions ────────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="read_vault_file",
            description="Read a markdown file from the Obsidian vault by its relative path. "
                        "Use this to inspect notes, rules, or any vault content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within vault (e.g. '0-规则层/GBrain_经验沉淀流程.md')",
                    },
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="write_vault_file",
            description="Create or overwrite a markdown file in the vault. "
                        "Path is relative to vault root. Automatically creates parent directories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within vault (e.g. '交付层/新文档.md')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content to write (with optional frontmatter)",
                    },
                },
                "required": ["path", "content"],
            },
        ),
        types.Tool(
            name="list_vault_files",
            description="List markdown files in a vault directory. "
                        "Returns file name, size, and last modified time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Relative directory path (e.g. '0-规则层' or '' for root).",
                        "default": "",
                    },
                },
            },
        ),
        types.Tool(
            name="search_vault",
            description="Search vault files by content (text) and/or filename pattern. "
                        "Useful for finding relevant notes across the knowledge base.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term to find in file contents",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename pattern (e.g. 'GBrain*' or '*规则*')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return (1-50)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="read_frontmatter",
            description="Extract YAML frontmatter from a markdown file. "
                        "Returns key-value pairs from the file header.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the markdown file",
                    },
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="get_vault_tree",
            description="Get the top-level directory structure of the vault. "
                        "Useful for orientation and navigation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "depth": {
                        "type": "integer",
                        "description": "How deep to traverse (1-3)",
                        "default": 2,
                    },
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

    try:
        if name == "read_vault_file":
            file_path = _resolve_path(arguments["path"])
            content = _read_markdown(file_path)
            return [types.TextContent(
                type="text",
                text=f"# {file_path.relative_to(VAULT_PATH)}\n\n{content}",
            )]

        elif name == "write_vault_file":
            rel_path = arguments["path"]
            content = arguments["content"]
            file_path = _resolve_path(rel_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return [types.TextContent(
                type="text",
                text=f"✅ 已写入: {rel_path} ({len(content)} 字节)",
            )]

        elif name == "list_vault_files":
            dir_arg = arguments.get("directory", "")
            dir_path = _resolve_path(dir_arg) if dir_arg else VAULT_PATH
            files = _list_files(dir_path)
            if not files:
                return [types.TextContent(type="text", text=f"目录为空: {dir_arg or '/'}")]
            lines = [f"## {dir_arg or '/'}\n"]
            for f in files:
                lines.append(f"- {f['name']}  ({f['size']}B)")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "search_vault":
            query = arguments["query"].lower()
            filename_pat = arguments.get("filename", "")
            max_results = min(arguments.get("max_results", 10), 50)

            results = []
            for fpath in VAULT_PATH.rglob("*.md"):
                if results and len(results) >= max_results + 5:
                    break
                # Filename filter
                if filename_pat:
                    import fnmatch
                    if not fnmatch.fnmatch(fpath.name, filename_pat):
                        continue
                # Content search
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    if query in content.lower():
                        rel = str(fpath.relative_to(VAULT_PATH))
                        # Find context around match
                        idx = content.lower().index(query)
                        start = max(0, idx - 40)
                        end = min(len(content), idx + len(query) + 40)
                        context = content[start:end].replace("\n", " ")
                        results.append({
                            "path": rel,
                            "context": context.strip(),
                        })
                except Exception:
                    continue

            if not results:
                return [types.TextContent(type="text", text=f"未找到包含 '{query}' 的文件。")]

            lines = [f"## 搜索结果: '{query}' ({len(results)})\n"]
            for r in results[:max_results]:
                lines.append(f"- **{r['path']}**")
                lines.append(f"  ...{r['context']}...\n")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "read_frontmatter":
            file_path = _resolve_path(arguments["path"])
            content = _read_markdown(file_path)
            fm = _parse_frontmatter(content)
            if not fm:
                return [types.TextContent(type="text", text="该文件没有 frontmatter。")]
            lines = [f"## Frontmatter: {arguments['path']}\n"]
            for k, v in fm.items():
                lines.append(f"- **{k}**: {v}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "get_vault_tree":
            depth = min(arguments.get("depth", 2), 3)
            lines = ["## Vault 目录结构\n"]
            root = VAULT_PATH

            def walk(dir_path: Path, level: int):
                if level > depth:
                    return
                indent = "  " * level
                for entry in sorted(dir_path.iterdir()):
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir():
                        lines.append(f"{indent}📁 {entry.name}/")
                        walk(entry, level + 1)
                    elif entry.suffix == ".md":
                        lines.append(f"{indent}📄 {entry.name}")

            walk(root, 0)
            return [types.TextContent(type="text", text="\n".join(lines))]

        else:
            raise ValueError(f"未知工具: {name}")

    except Exception as e:
        return [types.TextContent(type="text", text=f"Obsidian Bridge 错误: {str(e)}")]


# ── Entry point ─────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="obsidian-bridge",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
