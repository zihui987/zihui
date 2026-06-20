# GitHub 备份报告

> 日期：2026-06-20 | 状态：✅ 备份完成

---

## 远程仓库

| 项目 | 内容 |
|------|------|
| 远程地址 | `https://github.com/zihui987/zihui.git` |
| 分支 | `master` |
| 提交号 | `803313e` |
| 提交信息 | `feat: P0 基建收口 + 哈比星球规则初始备份` |
| 纳入文件数 | 66 |

---

## 备份范围

| 模块 | 路径 | 说明 |
|------|------|------|
| Hermes 配置 | `.hermes/config.yaml` | Hermes 主配置（含 kanban 段） |
| 三岗位 Profile | `.hermes/profiles/{xiaxia,xiaren,xiashen}/` | 3 个 profile 的 config.yaml + SOUL.md |
| 夏仁工作区 | `.agents/agent-夏仁/agent/` | AGENTS.md / SOUL.md / TOOLS.md |
| 夏审工作区 | `.agents/agent-夏审/agent/` | AGENTS.md / SOUL.md / TOOLS.md |
| P0 报告 | `P0收口报告/` | 全部 6 份报告（定版→确认→执行→回执） |
| MCP 代码 | `_temp_mcp_analysis/task_flow_mcp_server.py` | task_flow 只读桥接层（Phase 1 改造后） |
| 哈比星球规则 | `哈比星球/规则层/` | 全部规则文档 |
| 哈比星球运行中枢 | `哈比星球/运行中枢/` | Agent profile 配置 |
| 哈比星球技术配置 | `哈比星球/技术配置/` | MCP 服务脚本（不含 .db） |
| 哈比星球根文件 | `哈比星球/` | AGENTS.md / 智能体宪法.md / FILE_INDEX.md 等 |

---

## 排除清单

| 排除项 | 原因 |
|--------|------|
| `*.db`（kanban.db / task_flow.db / gbrain_data.db 等） | 运行态数据库，不备份 |
| `.hermes/sessions/` | 运行时会话 |
| `.hermes/processes.json` / `gateway_state.json` | 运行时状态快照 |
| `.agents/*/sessions/` | Agent 运行时会话 |
| `.agents/*/agent/.openclaw/` | 工作区运行时状态 |
| `__pycache__/` / `*.pyc` | Python 缓存 |
| `logs/` / `*.log` | 日志 |
| `Thumbs.db` / `.DS_Store` / `Desktop.ini` | 系统文件 |
| `node_modules/` | Node 依赖（不相关） |
| `_temp*/`（_temp_mcp_analysis 除外） | 临时目录 |

---

## Git 历史

```
803313e feat: P0 基建收口 + 哈比星球规则初始备份
```

---

## 当前状态总结

| 阶段 | 状态 |
|------|------|
| P0 定版 | ✅ 已签署 |
| Phase 1 执行（delegation toolset + 关 task_flow 写接口） | ✅ 已完成 |
| GitHub 备份 | ✅ 已完成 |
| Phase 2 配置（orchestrator_profile + mcp toolset + 审核路由） | 📋 待决策 |
| 恢复正式内容生产 | 🔒 未解锁 |

---

*报告人：Scream Code | 日期：2026-06-20*
