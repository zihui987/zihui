# AgentHub 可执行分析报告 — Hermes Agent 借鉴方案

> 基于 lizyoko9/bitdance-agenthub (224 commits, 5 agents, 9 DB tables)
> 分析日期: 2026-06-17

---

## 目录

1. [核心结论](#1-核心结论)
2. [五个最值得采纳的设计](#2-五个最值得采纳的设计)
3. [Agent 角色定义模板（可直接复制）](#3-agent-角色定义模板可直接复制)
4. [多 Agent 协作流水线](#4-多-agent-协作流水线)
5. [Orchestrator 调度机制拆解](#5-orchestrator-调度机制拆解)
6. [对 Hermes 的落地建议 & 优先级](#6-对-hermes-的落地建议--优先级)

---

## 1. 核心结论

| 维度 | AgentHub | Hermes 现状 | 借鉴价值 |
|------|----------|------------|---------|
| 消息模型 | `Message = Part[]` 数组，支持流式 delta | 文本/JSON | ⭐⭐⭐⭐⭐ |
| 事件协议 | `StreamEvent` 联合类型，30 种事件 | 无统一事件协议 | ⭐⭐⭐⭐⭐ |
| 多 Agent 协作 | Orchestrator DAG + 产物 handoff | `delegate_task` 平面并发 | ⭐⭐⭐⭐⭐ |
| Agent 角色定义 | System Prompt + 工具集精确绑定 | skill 体系 | ⭐⭐⭐⭐ |
| 审批流程 | review/auto 双模式 + 4 种审批类型 | 无 | ⭐⭐⭐⭐ |
| 安全沙箱 | 双平台黑名单 + workspace 配额 | 部分有 | ⭐⭐⭐ |

**一句话**：AgentHub 在"多 Agent 如何协作"这件事上比 Hermes 成熟——它有完整的 DAG 调度、产物交接、角色隔离。Hermes 在"单 Agent 能力"上更强（更多 tools、skills、memory），两边合并是完整方案。

---

## 2. 五个最值得采纳的设计

### 2.1 Message = Parts 数组

**AgentHub 做法**：每条消息不是纯文本，而是一个 `MessagePart[]` 数组：

```typescript
type MessagePart =
  | { type: 'text'; content: string }
  | { type: 'thinking'; content: string }
  | { type: 'code'; language: string; content: string }
  | { type: 'tool_use'; callId: string; toolName: string; args: unknown }
  | { type: 'tool_result'; callId: string; result: unknown }
  | { type: 'artifact_ref'; artifactId: string }
  | { type: 'deploy_status'; /* ... */ }
```

**增量流式**：`part.start` → `part.delta`（text.append / code.append / thinking.append）→ `part.end`

**为什么好**：
- 一条消息里可以混合文本、思考过程、代码块、工具调用、产物引用——各自独立渲染
- 前端渲染器按 type 分发到不同组件（TextPart / ThinkingPart / CodePart / ToolUsePart）
- 流式渲染天然支持：delta 直接追加到已有 part
- DB 存 JSON 数组，前后端同构

**Hermes 落地**：替换当前消息模型。效果——agent 的思考过程可以折叠、代码块语法高亮、工具调用独立展示。

### 2.2 StreamEvent 统一事件协议

**AgentHub 做法**：系统全生命周期 ≈ 30 种事件，统一为联合类型：

```typescript
type StreamEvent = BaseEvent & (
  | { type: 'run.start'; runId: string; agentId: string }
  | { type: 'message.start'; messageId: string }
  | { type: 'part.delta'; messageId: string; partIndex: number; delta: PartDelta }
  | { type: 'tool.call'; callId: string; toolName: string; args: unknown }
  | { type: 'tool.result'; callId: string; result: unknown }
  | { type: 'artifact.create'; artifactId: string }
  | { type: 'dispatch.plan.pending'; /* 审批 gate */ }
  | { type: 'fs_write.pending'; /* 文件写审批 */ }
  | /* ... 共 ~30 种 */
)
```

**架构**：Adapter → yield StreamEvent[] → AgentRunner → persist + EventBus.publish → SSE → Frontend store.applyEvent()

**Hermes 落地**：定义 Hermes 版的 StreamEvent，至少覆盖：`session.start/end`、`tool.call/result`、`message.part`、`delegate.start/end`、`error`。前端/日志/审计统一消费。

### 2.3 Agent 角色 = Prompt + Tools + 边界（三位一体）

**AgentHub 做法**：每个 agent 有 3 个绑定在一起的属性：

```typescript
{
  name: '前端工程师',
  systemPrompt: `你是前端工程师...\n\n工作方式：\n1. 先读上游产物\n2. 信息够就产\n\n必须包含：...（模板）`,
  toolNames: ['write_artifact', 'fs_write', 'bash', ...],
}
```

**关键设计**：
- **工具集精确限定**：前端工程师有 `fs_write` + `bash`，PM 小灰没有
- **Prompt 里内嵌输出模板**：UI 设计师的 prompt 里直接给了 `write_artifact({type:"document", title:"xxx", content:{...}})` 完整调用
- **工作方式列步骤**：每个 prompt 都有"工作方式：1. 2. 3."
- **自检清单**：调用工具前先自我检查参数完整性
- **否定约束明确**："不要写代码或新产物"、"禁止先空调用工具再补内容"

**Hermes 落地**：Hermes 的 skill 定义可以加上：
1. `allowed_tools` / `denied_tools`（工具白/黑名单）
2. `output_template`（输出模板，防 LLM 自由发挥）
3. `self_check`（调用前自检项）

### 2.4 审批模式（review/auto）

**AgentHub 做法**：4 种审批类型，统一的 Promise 等待模式：

```typescript
// 写文件审批
if (approvalMode === 'review') {
  const pending = pendingWrites.register({ path, content })
  yield { type: 'fs_write.pending', writeId: pending.id, path, content }
  const decision = await new Promise(resolve => {
    pendingWrites.attachResolver(pending.id, resolve)
    // abortSignal → 自动取消
    signal.addEventListener('abort', () => resolve('aborted'), { once: true })
  })
}
```

| 审批类型 | 事件 | 前端 UI |
|---------|------|--------|
| 写文件 | `fs_write.pending/resolved` | diff 面板 |
| Bash 命令 | `bash_command.pending/resolved` | 命令审批弹窗 |
| 调度计划 | `dispatch.plan.pending/resolved` | 计划卡片 |
| 用户提问 | `ask_user.pending/resolved` | 选项弹窗 |

**Hermes 落地**：对敏感工具（文件写、shell 执行、网络请求）加 review 模式。默认 auto 可配置。

### 2.5 DAG 多 Agent 调度 + 产物交接

**AgentHub 做法**：

```
PLAN → REVIEW GATE → EXECUTE(DAG) → (失败→重规划) → AGGREGATE
```

每个子任务格式：
```typescript
{
  id: 't1',
  agentId: 'ag_pm',
  task: '写 PRD...',
  dependsOn: [],
  expectedOutputs: [{ id: 'prd', type: 'document' }],  // 产物契约
  acceptanceCriteria: ['覆盖所有 P0 功能', '验收标准可测试'],
}
```

下游通过 `inputs: [{ fromTaskId: 't1', outputId: 'prd' }]` 引用上游产物。

**为什么好**：
- 不是简单并发，是带依赖图的有序执行
- 产物显式声明 → 类型安全的手递手
- 同波次冲突检测（多 agent 写同一文件不同内容）
- 动态重规划带失败上下文，不是无脑重试

**Hermes 落地**：`delegate_task` 加 `dependsOn` 和 `outputs` 参数，从"平面并发"升级为"DAG 流水线"。

---

## 3. Agent 角色定义模板（可直接复制）

### 3.1 Orchestrator（协调器）

```typescript
{
  name: 'Orchestrator',
  systemPrompt: `你是协调者。负责理解用户目标，决定是否需要多 Agent 协作。

调度原则：
1. 简单问题直接回答；只有需要多角色产出、并行处理时才分派
2. 子任务要面向结果，不要规定过细流程
3. 根据 Agent 能力选择负责人
4. 产物链路要清楚：PRD -> 设计 -> 实现 -> 审查
5. 聚合结果时只总结关键结论和下一步决策`,
  tools: ['delegate_task', 'ask_user', 'fs_read', 'read_attachment'],
  // 注意：没有 fs_write / bash
}
```

### 3.2 产品经理（需求）

```typescript
{
  name: 'PM',
  systemPrompt: `你是产品经理。核心产出是 PRD。

工作方式：
1. 先读上游产物或附件
2. 信息够直接产出；缺失关键需求时最多提 3 个问题
3. PRD 必须包含：目标用户、问题背景、核心功能(P0/P1/P2)、非功能要求、范围边界、验收标准`,
  tools: ['write_artifact', 'read_artifact', 'read_attachment', 'ask_user', 'fs_read'],
}
```

### 3.3 设计师（UI/视觉）

```typescript
{
  name: 'UI Designer',
  systemPrompt: `你是 UI 设计师。核心产出是风格指南。

工作方式：
1. 先读上游 PRD / 参考图
2. 不做空泛审美描述，给可落地的视觉参数
3. 风格指南必须包含：配色(hex)、字体层级、间距圆角阴影参数、组件规范、交互状态

自检：type 必须是 "document"，title 是非空字符串，content 包含 markdown 正文`,
  tools: ['write_artifact', 'read_artifact', 'read_attachment', 'ask_user', 'fs_read'],
}
```

### 3.4 工程师（实现）

```typescript
{
  name: 'Engineer',
  systemPrompt: `你是工程师。可修改本地文件或创建可预览产物。

工作方式：
- 本地代码项目：用 fs_read/write/bash 直接操作文件
- 网页产物：用 write_artifact(type='web_app') 创建，然后 deploy_artifact

要求：
1. 先读上游 PRD / 设计
2. 实现所有 P0 功能
3. 完成后必须调用 deploy_artifact 生成预览`,
  tools: ['write_artifact', 'deploy_artifact', 'read_artifact', 'fs_read', 'fs_write', 'bash', 'ask_user'],
}
```

### 3.5 Reviewer（审查）

```typescript
{
  name: 'Reviewer',
  systemPrompt: `你是 Reviewer。审查产物和代码是否满足需求。

你必须：
1. 先读相关产物和代码
2. 对照用户目标、PRD、设计和实现的一致性
3. 问题按严重程度排序，给出「问题/影响/建议」
4. 无明显问题时写明"未发现阻塞问题"

不要写代码或新产物，只输出审查报告。`,
  tools: ['read_artifact', 'fs_read', 'bash', 'ask_user'],
  // 注意：没有 write_artifact、fs_write
}
```

---

## 4. 多 Agent 协作流水线

### 4.1 完整协作流程

```
┌─────────────────────────────────────────────────────────────────┐
│ 用户请求："帮我做一个待办事项网页应用"                           │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ Orchestrator：理解需求 → 拆任务 → 调用 plan_tasks               │
│ 输出 Plan：                                                    │
│   t1: PM 写 PRD (dependsOn:[])                                 │
│   t2: 设计师出风格指南 (dependsOn:[t1])                         │
│   t3: 前端工程师实现 (dependsOn:[t1, t2])                      │
│   t4: Reviewer 审查 (dependsOn:[t3])                           │
└─────────────────────────────────────────────────────────────────┘
                               │
                     ┌─────────▼─────────┐
                     │   REVIEW GATE      │ ← 用户确认计划
                     │   (批准/修改/拒绝)  │
                     └─────────┬─────────┘
                               │ 批准
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ WAVE 1 (并行):                                                  │
│   ┌─────────────────┐                                           │
│   │ PM 写 PRD       │ → write_artifact(outputKey='prd')        │
│   └─────────────────┘                                           │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ WAVE 2 (并行):                                                  │
│   ┌──────────────────────┐                                      │
│   │ 设计师出风格指南      │ → write_artifact(outputKey='design')│
│   └──────────────────────┘                                      │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ WAVE 3 (单个):                                                  │
│   ┌──────────────────────────┐                                  │
│   │ 前端工程师实现网页        │ → fs_write(代码)                │
│   │ inputs: [prd, design]    │ → deploy_artifact               │
│   └──────────────────────────┘                                  │
│   ┌──────────────────────────┐                                  │
│   │ ← 冲突检测：本波无冲突    │                                  │
│   └──────────────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ WAVE 4 (单个):                                                  │
│   ┌──────────────────────────┐                                  │
│   │ Reviewer 审查           │ → 文本审查报告                    │
│   │ input: [web_app]        │                                  │
│   └──────────────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ AGGREGATE: Orchestrator 收集所有任务结果 → 输出总结             │
│ "已完成：PRD、风格指南、待办网页已部署在 localhost:xxxx"        │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 失败 + 重规划流程

```
WAVE 3 执行 → 前端工程师写文件冲突（与另一个子任务改了同一文件）
       ↓
detectWaveConflicts() → 返回 [FileWriteConflict{path, writers: [t3, t5]}]
       ↓
shouldReplan() → 本轮有冲突，未超 MAX_DISPATCH_ROUNDS=4
       ↓
buildReplanContext() → XML 包装冲突详情
       ↓
回到 PLAN 阶段 → Orchestrator 决策：串行重做冲突任务
       ↓
WAVE 3R: 只重做冲突的 t5（这次没有 t3 并行）
       ↓
成功 → 继续 AGGREGATE
```

---

## 5. Orchestrator 调度机制拆解

### 5.1 代码层面的三阶段

| 阶段 | 函数 | 位置 |
|------|------|------|
| PLAN | `runPlanStage()` | agent-runner.ts:581 |
| EXECUTE | `executeDag()` | agent-runner.ts:724 |
| AGGREGATE | `buildAggregatePrompt()` | agent-runner.ts:2461 |

### 5.2 各阶段关键行为

**PLAN 阶段**（~L581-720）：
1. 构建 `buildOrchestratorPlanPrompt()` → 系统提示里注入其他 Agent 列表（id/name/capabilities/tools/description）
2. 调用 LLM，工具集仅限 `plan_tasks` + 只读工具
3. `parseDispatchPlanToolArgs()` → 解析 `plan_tasks` 调用参数 → `compileDispatchPlan()` 推断缺失依赖
4. `validateDispatchPlan()` → 环检测 + 语义校验
5. `waitForDispatchPlanReview()` → 用户审批 gate

**EXECUTE 阶段**（~L724-870）：
1. 拓扑排序 → 按 `dependsOn` 分波次
2. 每个波次：`Semaphore(MAX_CONCURRENT=4)` 并发
3. 每个子任务 = 独立的 `AgentRunner.run()` + `buildSubAgentPrompt()`（隔离上下文）
4. 子任务必须调 `report_task_result` 上报，否则判 failed
5. `evaluateChildTaskResult()` → 验收评估
6. 每波结束 → `detectWaveConflicts()` → 冲突检测
7. `shouldReplan()` → 决定是否重规划

**AGGREGATE 阶段**（~L539-580）：
1. Orchestrator 再去掉 `plan_tasks` 工具
2. 注入 `<task_results>` XML 块，包含所有任务状态/产物/错误
3. 注入 `<file_conflicts>` 块（如有冲突）
4. LLM 输出最终消息

### 5.3 子任务隔离原则

子 agent 收到的是**隔离上下文**，不是完整群聊历史：

```xml
<task>
你被分配的任务：实现待办事项网页
</task>

<upstream_artifacts>
<artifact id="prd" type="document">PRD 内容...</artifact>
<artifact id="design" type="document">风格指南内容...</artifact>
</upstream_artifacts>

<recent_conversation>
用户原始需求...
</recent_conversation>

<pinned_messages>
... 置顶消息 ...
</pinned_messages>
```

子 agent 看不到其他 agent 的完整对话、中间过程、失败历史——只看到输入物和自己的任务。

---

## 6. 对 Hermes 的落地建议 & 优先级

### P0 — 立刻可以做的（低投入高收益）

| # | 项目 | 改动量 | 说明 |
|---|------|--------|------|
| 1 | **Agent Prompt 模板化** | 小 | 参考 AgentHub 的 prompt 结构（工作方式→模板→自检），优化 Hermes 的 skill prompt |
| 2 | **工具集按角色限定** | 中 | skill 定义里加 `allowed_tools` / `denied_tools` 字段 |
| 3 | **子任务产物 handoff** | 中 | `delegate_task` 加 `outputs` 参数，让子任务的产出物可被其他任务引用 |
| 4 | **审批模式** | 中 | 对 `terminal` 加 review 开关，敏感命令需确认再执行 |

### P1 — 值得投入的（中投入中收益）

| # | 项目 | 改动量 | 说明 |
|---|------|--------|------|
| 5 | **Message Parts 数组** | 大 | 替换 Hermes 当前消息模型，text/thinking/code/tool_call 各自独立渲染 |
| 6 | **`dependsOn` DAG 调度** | 中 | `delegate_task` 支持任务依赖关系，自动拓扑排序 + 波次执行 |
| 7 | **子任务隔离上下文** | 中 | `delegate_task` 的子 agent 只看到任务描述 + 输入物，不看完整 parent 历史 |
| 8 | **同波次冲突检测** | 小 | 多个子任务写同一文件时自动检测并报告 |

### P2 — 长期考虑的（大投入高收益）

| # | 项目 | 改动量 | 说明 |
|---|------|--------|------|
| 9 | **StreamEvent 事件协议** | 大 | 定义 Hermes 生命周期事件类型，统一前后端协议 |
| 10 | **SSE 实时推送** | 大 | 替代轮询/WebSocket，事件流天然适配前端状态管理 |
| 11 | **Orchestrator 三阶段调度** | 大 | PLAN→EXECUTE→AGGREGATE 完整流水线 + 动态重规划 |

### 6.1 推荐实施路径

```
第 1 步（本周可做）：
  - 优化 Hermes skill 的 prompt 结构（加工作方式 + 自检清单）
  - skill 定义加 tool whitelist

第 2 步（本月）：
  - delegate_task 加 dependsOn / outputs（DAG 化）
  - 子任务隔离上下文
  - terminal 加 review 模式

第 3 步（季度）：
  - Message Parts 数组模型
  - StreamEvent 事件协议
  - SSE 推送
```

---

## 附录：AgentHub 源码参考位置

| 文件 | 内容 | 行数 |
|------|------|------|
| `src/server/agent-runner.ts` | Orchestrator 核心调度 | ~2600 |
| `src/server/tools/plan-tasks.ts` | DAG plan 工具 | ~200 |
| `src/server/dispatch-file-writes.ts` | 冲突检测 | ~150 |
| `src/server/dispatch-plan.ts` | Plan 编译 + 校验 | ~200 |
| `src/server/conversation-context.ts` | 历史序列化 | ~300 |
| `src/server/adapters/types.ts` | Adapter 接口 | ~80 |
| `src/server/adapters/custom-agent-adapter.ts` | 自驱 tool loop | ~300 |
| `src/server/tools/fs-write.ts` | 文件写 + 审批 | ~200 |
| `src/server/security.ts` | 黑名单 | ~150 |
| `src/server/workspace-utils.ts` | 沙箱 | ~100 |
| `src/shared/types.ts` | StreamEvent + MessagePart | ~600 |
| `src/db/builtin-agents.ts` | 5 个内置 Agent 定义 | ~180 |
| `src/stores/app-store.ts` | Zustand reducer | ~600 |
| `src/components/stream-provider.tsx` | SSE 客户端 | ~80 |
