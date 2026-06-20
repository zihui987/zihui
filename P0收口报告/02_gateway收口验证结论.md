# Gateway 收口验证结论

> 本文件为 P0 基建收口专项 — 第二单：gateway 收口验证
> 日期：2026-06-20 | 状态：已出结论

---

## 1. 当前状态总览

| 项目 | 状态 |
|------|------|
| Gateway 进程 | ✅ 运行中（pid 17404/22080） |
| 平台连接 | ✅ API Server / 飞书 / 微信 / 元宝 均已连接 |
| Active Agents | ⚠️ 0（当前无派遣中的 agent） |
| Kanban Dispatch | ✅ 已启用（60s 间隔） |
| Auto Decompose | ✅ 已启用（每次 3 条） |
| 默认指派 | xiaren |

---

## 2. 四个核心问题答案

### Q1: 调度入口是谁？

| 问题 | 当前状态 | 结论 |
|------|----------|------|
| 谁创建任务 | **夏夏（主 Hermes 会话）** | 夏夏通过 MCP tools 或 kanban 接口创建任务 |
| 任务进入哪套系统 | **当前进入 task_flow（11条生产任务）** | ⚠️ 应改为 kanban，当前为双轨偏离 |

**关键发现**：从数据库实盘来看，夏夏在 task_flow 中创建了 11 条任务，而在 kanban 中仅创建了 1 条测试任务。调度入口实际走的是 task_flow MCP，而非设计预期的 kanban。

**收口目标**：夏夏创建任务应统一走 kanban。

### Q2: 认领机制是什么？

| 问题 | 当前状态 | 结论 |
|------|----------|------|
| 夏仁如何看到并认领 | **当前：夏夏手动委派** | 非自动认领 |
| 夏审如何接收审核任务 | **当前：夏夏手动通知** | 非自动认领 |
| 是否有自动认领 | ❌ 无 | Kanban dispatch 配置了 60s 间隔，但绑定字段 `orchestrator_profile` 为空 |

**关键发现**：
- `kanban.orchestrator_profile` 为空字符串，意味着 gateway 没有配置编排者 profile
- 默认指派只有 `xiaren`，没有配置 `xiashen` 的审核任务路由
- 当前实际流程是：夏夏（主会话）通过**人肉编排**来串起夏仁→夏审的流转

### Q3: 状态推进由谁负责？

| 状态 | 当前推进者 | 自动化程度 |
|------|-----------|-----------|
| created | 夏夏（手动写 task_flow API） | ❌ 手动 |
| pending → assigned | 夏夏 | ❌ 手动 |
| assigned → in_review | 夏夏 | ❌ 手动 |
| in_review → completed | 夏夏 | ❌ 手动 |
| completed → archived | 夏夏 | ❌ 手动 |
| review 审核结论 | 夏审 | ⚠️ 半自动（夏审独立审核，但状态由夏夏更新） |

**关键发现**：当前所有状态推进都由 夏夏 手动执行。task_flow 虽然设计了状态机（TRANSITIONS），但实际并未实现自动流转——因为 profile worker 无法调用 task_flow MCP 工具。

**profile config 验证**：
```
夏仁 toolsets: file, terminal, search, todo    ← 无 mcp
夏审 toolsets: file, search, todo               ← 无 mcp
夏夏 toolsets: file, terminal, search, delegation, todo  ← 有 delegation
```

### Q4: 人工确认点在哪里？

| 环节 | 当前 | 应否自动化 | 说明 |
|------|------|-----------|------|
| 任务创建 | 人工（夏夏） | ✅ 可自动 | 夏夏决策后创建 |
| 任务派发 | 人工（夏夏） | ✅ 可进入 kanban dispatch | 当前绕过 kanban 走 task_flow |
| 夏仁执行 | 自动（profile worker） | ✅ 已自动 | 夏仁独立执行能力已验证 |
| 执行→审核交接 | 人工（夏夏） | ⚠️ 需设计 | 需要明确的 handoff 机制 |
| 夏审审核 | 自动（profile worker） | ✅ 已自动 | 夏审独立审核能力已验证 |
| 审核结论确认 | 人工（夏夏） | ⚠️ 需确认 | 重要节点，建议保留人工确认 |
| 归档 | 人工（夏夏） | ✅ 可自动 | 审核通过后的归档操作 |

---

## 3. 当前可自动化环节

| 环节 | 可行性 | 前置条件 |
|------|--------|----------|
| 任务进入 kanban | ✅ 高 | 夏夏改用 kanban create 替代 task_flow create |
| 派发至夏仁 | ✅ 高 | kanban dispatch 已配置，default_assignee: xiaren |
| 执行→审核流转 | ⚠️ 中 | 需要配置 kanban 的路由策略或通过 delegator 实现 |
| 审核通过后归档 | ✅ 高 | kanban 支持 status 变更事件 |
| 审核打回重做 | ⚠️ 中 | 需定义 returned 状态在 kanban 中的等价映射 |

## 4. 当前必须人工推进的环节

| 环节 | 理由 |
|------|------|
| 任务类型/优先级决策 | 需要夏夏判断生产内容、排期、优先级 |
| 审核不通过的裁决 | 审核打回后是否重派、是否变更需求，需夏夏决策 |
| gateway 整体编排 | 当前 orchestrator_profile 未配置，无自动编排者 |

## 5. 当前断链点

| 断链 | 根因 | 影响 |
|------|------|------|
| ① 任务创建走错系统 | 夏夏习惯/工具链指向 task_flow 而非 kanban | 主任务流不在 kanban |
| ② 无 orchestrator profile | `kanban.orchestrator_profile: ''` | 审核任务无法自动路由至夏审 |
| ③ Profile 无 MCP 工具集 | 三个 profile 均无 `mcp` toolset | Profile 无法读写任意 MCP 服务 |
| ④ 审核回执不自动 | 夏审只能输出文件结论，不能直接更新任务状态 | 状态更新依赖夏夏手动写入 |

## 6. 最小可行收口方案

### 第一阶段（当前即可执行）

1. **Profile 配置更新**：给 xiaren 和 xiashen profile 添加 `delegation` toolset（参考 xiaxia 配置），使其能接收委派任务
2. **任务创建指向 kanban**：夏夏从 task_flow create 切换到 kanban create
3. **关闭 task_flow 写入口**：限制 task_flow MCP 的 create_task/update_task_status/handoff_task 工具调用

### 第二阶段（需 Hermes 配置调整）

4. **配置 orchestrator_profile**：将 `kanban.orchestrator_profile` 设置为 `xiaxia`，使 gateway 有编排者
5. **Profile 补充 mcp toolset**：给 xiaren 和 xiashen 添加 `mcp`，使其能直接调用 MCP 工具
6. **定义审核路由**：在 kanban 中配置审核状态流转规则

### 第三阶段（后续方向）

7. **task_flow 数据迁移**：将现有 11 条生产任务的审计记录导入 kanban 或备份
8. **task_flow 只保留只读模式**：确认 task_flow MCP 只作为查询接口

## 7. 后续是否需要继续改 Hermes 配置

**是**。至少需要以下更改：

| 配置项 | 当前值 | 应改为 | 优先级 |
|--------|--------|--------|--------|
| `kanban.orchestrator_profile` | `''` | `xiaxia` | P0 |
| `kanban.default_assignee` | `xiaren` | 保持不变 | - |
| `profiles/xiaren/config.yaml → toolsets` | 无 `mcp` | 添加 `mcp` | P1 |
| `profiles/xiashen/config.yaml → toolsets` | 无 `mcp` | 添加 `mcp` | P1 |
| `profiles/xiaren/config.yaml → toolsets` | 无 `delegation` | 添加 `delegation` | P1 |

---

## 附录：认证数据来源

- Gateway 状态：`~/.hermes/gateway_state.json`
- Kanban 配置：`~/.hermes/config.yaml` lines 495-506
- Profile 配置：`~/.hermes/profiles/{xiaxia,xiaren,xiashen}/config.yaml`
- 生产任务数据：`E:/哈比星球/技术配置/task_flow.db`（11条任务，51条日志）
- Kanban 任务数据：`~/.hermes/kanban.db`（1条测试任务）
- 主进程信息：`~/.hermes/processes.json`

---

*验证人：Scream Code P0 收口专项 | 日期：2026-06-20*
