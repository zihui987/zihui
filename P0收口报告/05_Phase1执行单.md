# Phase 1 执行单

> 本轮性质：基建改造单 | P0 收口 Phase 1
> 日期：2026-06-20 | 前置依赖：04_P0定版确认单.md ✅ 已签署

---

## 一、执行边界

- 只做确认单中 Phase 1 的三项动作
- 不进入 GitHub 备份阶段
- 不恢复正式内容生产
- 不夹带其他配置改动
- 每项动作必须有回执和验证

---

## 二、Phase 1 三项任务

### 任务 1：给 xiaren / xiashen 补 delegation toolset

| 项目 | 内容 |
|------|------|
| 目标 | 使 xiaren / xiashen profile 能接收委派任务 |
| 参考 | xiaxia profile 已有 `delegation` toolset |
| 操作 | 修改 `profiles/xiaren/config.yaml` 和 `profiles/xiashen/config.yaml` 的 `toolsets` 字段 |
| 验证标准 | 两个 profile 的 config 中 toolsets 包含 `delegation` |

### 任务 2：将任务创建入口从 task_flow 切到 kanban

| 项目 | 内容 |
|------|------|
| 目标 | 新任务创建统一走 Hermes 内建 kanban，不再走 task_flow MCP |
| 操作 | 夏夏创建任务时改为调用 kanban API，不再使用 task_flow create_task |
| 验证标准 | 新任务出现在 `kanban.db` 中，不再新增到 `task_flow.db` |

### 任务 3：关闭 task_flow 写接口

| 项目 | 内容 |
|------|------|
| 目标 | 限制 task_flow MCP 服务仅开放只读接口，关闭写能力 |
| 操作 | 修改 task_flow_mcp_server.py，移除或注释 create_task、update_task_status、handoff_task 等写接口的 tool 注册 |
| 验证标准 | task_flow MCP 的 `get_task`、`list_tasks`、`get_task_stats` 仍可用，写接口返回不可用 |

---

## 三、回执格式

每项完成后输出以下格式的回执：

```md
## 任务 X 执行回执

- **动作：** <具体做了什么>
- **变更文件：** <文件路径>
- **变更前：** <关键行内容>
- **变更后：** <关键行内容>
- **验证结果：** ✅ / ❌
- **验证方式：** <实际验证命令/操作>
```

---

## 四、完成条件

1. ✅ 三项任务均有回执
2. ✅ 三项验证结果均为通过
3. ✅ 确认不越界（未动备份、未动内容生产、未夹带其他改动）

---

*执行人：夏夏 | 监督人：你 | 日期：2026-06-20*
