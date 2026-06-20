# 四系统整合方案：Scream Code × Codex CLI × Claude Code × Hermes

> 调研日期：2026-06-19
> 状态：初版完成，待讨论

---

## 目录

1. [各系统核心能力盘点](#1-各系统核心能力盘点)
2. [上下文管理与记忆系统 — 关键差距分析](#2-上下文管理与记忆系统--关键差距分析)
3. [整合架构方案](#3-整合架构方案)
4. [关键组件设计](#4-关键组件设计)
5. [实施路径](#5-实施路径)
6. [风险评估与取舍](#6-风险评估与取舍)

---

## 1. 各系统核心能力盘点

### 1.1 Scream Code v0.5.12

| 维度 | 详情 |
|------|------|
| **语言** | TypeScript (Node.js ≥22.19) |
| **授权** | MIT，个人开发者 LIUTod |
| **定位** | 轻量 Agent 底座，中文本地化优先 |

**核心能力：**

- **状态机任务调度** — 最独特的价值。把任务分解为有限状态，防漂移能力强。FullCompaction + MicroCompaction 两层压缩机制
- **记忆系统** — SQLite 持久化，`MemoryLookup` 和 `MemoryWrite` 工具暴露给模型。支持 `/dream` 去重合并。可选 `fastembed` 向量嵌入（但安装失败时静默降级为关键词搜索）
- **并行子 Agent** — coder/explore/plan/verify/writer 五种角色，WolfPack 群狼模式
- **MCP 集成** — 支持标准 MCP 协议
- **模型无关** — 多 provider 网关，可自由切换模型

**关键限制：**

- 上下文管理只有粗暴压缩（LLM 总结旧消息），没有层级化、优先级保留或向量检索
- 记忆系统检索是关键词 + 可选向量，非深度语义检索；串行写锁可能成为瓶颈
- `fastembed` 安装失败时静默降级，用户无感知
- 无沙箱执行环境
- 个人项目，<70 stars，API 未稳定

### 1.2 Codex CLI (OpenAI)

| 维度 | 详情 |
|------|------|
| **语言** | Rust (核心引擎) + TypeScript |
| **授权** | MIT，OpenAI 团队 |
| **定位** | OpenAI 官方参考实现 |

**核心能力：**

- **沙箱隔离** — sandbox-bin 通过独立 runner 进程执行命令，安全策略最严谨
- **插件市场** — `codex-core-plugins` crate，支持从归档下载、解压、MCP 配置。官方维护插件列表（browser、LaTeX、iOS 开发等）
- **上下文管理** — `context/mod.rs` 约 40 个模块，分片式上下文管理
- **线程与会话管理** — 完整生命周期，持久化存储
- **Rust 性能** — 核心引擎 Rust 编写，性能开销低

**关键限制：**

- 模型绑定 OpenAI，无法切换 provider
- 插件市场 API（发现、搜索、发布）是服务端闭源的
- Windows 沙箱支持有限（主要面向 macOS/Linux）
- 闭源 PR 政策，社区贡献受限
- 无跨会话记忆系统

### 1.3 Claude Code (Anthropic)

| 维度 | 详情 |
|------|------|
| **语言** | TypeScript (闭源) |
| **授权** | 专有，商业产品 |
| **定位** | 最佳开箱即用体验 |

**核心能力：**

- **服务端压缩 (compact_20260112)** — Anthropic 独有的上下文压缩 API，在 API 层面完成，比客户端压缩更高效
- **Project Knowledge** — 项目级知识库，可注入对话上下文
- **权限架构** — auto/manage 模式分级，基于分类器的风险决策
- **MCP 延迟加载** — MCP 服务器懒加载，减少启动开销
- **Claude 模型深度集成** — 模型与工具调用的协同优化最好
- **提示缓存** — per-conversation prompt caching，减少 API 成本

**关键限制：**

- 闭源 + 收费，不可定制
- 只能用 Claude 模型
- 无本地持久化记忆系统
- 全部请求走 Anthropic API

### 1.4 Hermes Agent v0.16.0

| 维度 | 详情 |
|------|------|
| **语言** | Python |
| **授权** | MIT，Nous Research |
| **定位** | 多平台 AI Agent 框架 |

**核心能力：**

- **子 Agent 编排 (`delegate_task`)** — 最成熟的子 Agent 系统。独立会话、工具痕迹、证据链。1托2（调度→执行→审查）模式
- **插件生态** — 丰富的 plugins 目录（memory、model-providers、kanban、image_gen 等）
- **多平台网关** — Telegram、Discord、Slack、企业微信、飞书等 20+ 平台
- **上下文压缩引擎** — `context: engine: compressor`，插件化的上下文引擎
- **记忆系统** — 支持 agentmemory 等多种 provider
- **提示缓存保护** — 设计决策中「per-conversation prompt caching 不可侵犯」
- **Kanban 看板调度** — 多 Agent 任务板的调度器 + 工作线程
- **ACP 协议** — 支持 VS Code / Zed / JetBrains 集成

**关键限制：**

- Python 性能瓶颈（GIL），CPU 密集任务受限
- 线程级并行，主要适合 I/O 密集型子任务
- 配置复杂度高（config.yaml 657 行）
- 不支持沙箱执行
- 1托2 是用户层面的模式命名，非 Hermes 原生产物

---

## 2. 上下文管理与记忆系统 — 关键差距分析

这是整个整合方案的核心痛点，所以单独深入分析。

### 2.1 现有方案的能力地图

| 能力维度 | Scream Code | Codex CLI | Claude Code | Hermes |
|----------|------------|-----------|-------------|--------|
| 上下文压缩 | ✅ 全量+微压缩 | ✅ 分片压缩 | ✅ 服务端压缩 | ✅ 插件化压缩 |
| 层级化上下文 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| 向量检索记忆 | ⚠️ 可选(fastembed) | ❌ 无 | ❌ 无 | ✅ 插件化 |
| 结构化记忆(经验) | ✅ SQLite Memo | ❌ 无 | ⚠️ Project Knowledge | ✅ 多种 provider |
| 跨会话共享 | ✅ 记忆系统 | ❌ 无 | ❌ 无 | ⚠️ 依赖插件 |
| 自动老化/去重 | ✅ dream 整理 | ❌ 无 | ❌ 无 | ❌ 无 |
| 关键信息优先级保留 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| 因果/依赖关系记忆 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |

**关键发现：没有任何一个系统真正解决了长项目的上下文管理问题。** Scream Code 的记忆框架最接近，但缺乏向量深度和优先级机制。

### 2.2 学术界最新进展

调研发现以下值得关注的技术方向：

1. **AgingBench (2025)** — 系统性定义了四种"老化"机制：压缩干扰、信息干扰、修订漂移、维护遗忘。对 coding agent 的可靠性有直接指导意义
2. **MemGPT / Letta** — 4 层内存架构（工作记忆/情景记忆/程序记忆/长期记忆），OS 式内存管理
3. **ContextWeaver (2025)** — 依赖结构化的记忆图，保持推理步骤间的因果逻辑关系，比简单压缩更好
4. **GitOfThoughts (2025)** — 把推理链当 Git 分支管理，支持合并/回滚/分支
5. **SuperLocalMemory (2024)** — 通过代码结构感知的局部性优先策略，显著减少无关上下文污染

---

## 3. 整合架构方案

### 3.1 核心设计原则

1. **上下文管理层独立成层** — 不做任何系统的附属，而是所有 agent 共享的基础设施
2. **模型与框架解耦** — 用最好的模型（Claude）做推理，不做框架绑定的"大脑"
3. **各取所长** — 每个系统只负责它做最好的那件事
4. **MCP 作为通用胶水协议** — 所有跨系统通信通过 MCP，而不是互相调用私有 API

### 3.2 整体架构

```
                    ┌──────────────────────────────────────┐
                    │          用户交互层                    │
                    │  [Scream TUI / Hermes Gateway / CLI]  │
                    └──────────────┬───────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────┐
                    │        编排调度层 (Orchestrator)       │
                    │                                      │
                    │  ┌────────────────────────────────┐  │
                    │  │    Hermes delegate_task 引擎    │  │
                    │  │  (最成熟的子Agent编排)           │  │
                    │  │  1托2 / DAG / WolfPack 模式     │  │
                    │  └──────────────┬─────────────────┘  │
                    │                                      │
                    │  ┌──────────────▼─────────────────┐  │
                    │  │    Scream 状态机 (状态追踪)     │  │
                    │  │  只负责"当前在什么状态"，       │  │
                    │  │  不负责理解上下文               │  │
                    │  └────────────────────────────────┘  │
                    └──────────────┬───────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────┐
                    │       上下文管理层 ★核心新增★          │
                    │                                      │
                    │  ┌────────────────────────────────┐  │
                    │  │  1. 层级化上下文管理器            │  │
                    │  │     ├─ Hot层 (当前窗口, ~8K tokens)│  │
                    │  │     ├─ Warm层 (会话摘要, ~实时)   │  │
                    │  │     └─ Cold层 (项目知识, 按需检索) │  │
                    │  │                                      │
                    │  │  2. 因果记忆图                     │  │
                    │  │     ├─ 决策节点: "为什么这么做"     │  │
                    │  │     ├─ 事实节点: "确认了哪些事实"   │  │
                    │  │     └─ 拒绝节点: "排除了哪些方案"   │  │
                    │  │                                      │
                    │  │  3. 向量存储 (LanceDB / SQLite-vec) │  │
                    │  │     ├─ 代码语义检索                │  │
                    │  │     ├─ 历史决策检索                │  │
                    │  │     └─ 踩坑经验检索                │  │
                    │  └────────────────────────────────┘  │
                    └──────────────┬───────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
┌─────────▼─────────┐  ┌──────────▼──────────┐  ┌──────────▼──────────┐
│   执行沙箱层       │  │  记忆持久化层        │  │   模型网关           │
│                    │  │                     │  │                     │
│  Codex Sandbox     │  │  Scream SQLite      │  │  Claude(深度推理)    │
│  Runner            │  │  (结构化Memo)        │  │  GPT-4o(常规编码)    │
│  (命令隔离)        │  │                     │  │  DeepSeek(批量)      │
│                    │  │  LanceDB向量库       │  │  通义千问(中文场景)   │
│  Codex 插件市场    │  │  (代码+决策语义)      │  │                     │
│  (浏览器/LaTeX等)  │  │                     │  │  统一模型网关         │
│                    │  │  Hermes 记忆插件     │  │  (Scream multi-      │
│  Scream MCP 桥梁   │  │  (多种provider)      │  │   provider 机制)     │
│                    │  │                     │  │                     │
└────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

### 3.3 系统职责分配

| 职责 | 承担系统 | 理由 |
|------|---------|------|
| **推理核心** | Claude 模型 (API) | 上下文能力最强，指令遵循最好 |
| **子Agent编排** | Hermes delegate_task | 最成熟的实现，独立会话+证据链 |
| **任务状态追踪** | Scream 状态机（降级使用） | 防漂移，但不承担理解责任 |
| **命令执行沙箱** | Codex sandbox-runner | 唯一有严谨沙箱的方案 |
| **扩展能力** | Codex 插件市场 | 官方的插件体系最完整 |
| **MCP 桥梁** | Scream MCP 层 | 已有标准 MCP 实现 |
| **结构化记忆** | Scream Memory SQLite | 已有完整的 Memo + Dream 整理 |
| **向量记忆** | LanceDB / SQLite-vec | 新增组件，全系统共享 |
| **错误预防** | Hermes 循环保护 | 看板调度 + tool_loop_guardrails |
| **多平台交互** | Hermes Gateway | 20+ 平台支持，cc-connect 即用 |
| **终端交互** | Scream TUI | 中文支持最好 |

---

## 4. 关键组件设计

### 4.1 上下文管理层 — 核心新组件

这是整个方案中**必须从零构建**的组件，现有四个系统都做不好。

**层级设计：**

```
                    Hot Layer (当前工作窗口)
                    ┌──────────────────────────┐
                    │  ~8K tokens 滑动窗口       │
                    │  当前任务 + 最近3轮交互     │
                    │  关键决策摘要注入           │
                    └──────────┬───────────────┘
                               │ 溢出时压缩
                    ┌──────────▼───────────────┐
                    │  Warm Layer (会话摘要)    │
                    │  LLM 自动生成的层级摘要     │
                    │  每 N 轮或每状态转换时刷新  │
                    │  决策节点 + 事实节点注入    │
                    └──────────┬───────────────┘
                               │ 按需检索
                    ┌──────────▼───────────────┐
                    │  Cold Layer (项目知识)    │
                    │  向量存储 (LanceDB)       │
                    │  代码语义 + 历史决策 + 踩坑 │
                    │  MemoryLookup 式按需注入   │
                    └──────────────────────────┘
```

**因果记忆图数据模型：**

```typescript
interface DecisionNode {
  id: string;
  type: 'decision' | 'fact' | 'rejection' | 'assumption';
  timestamp: number;
  sessionId: string;
  content: string;         // 决策/事实的描述
  context: string;         // 当时的上下文（代码片段、对话摘要）
  parentIds: string[];     // 依赖的上游节点
  status: 'active' | 'superseded' | 'invalidated';
  supersededBy?: string;   // 被哪个新决策替代
}
```

**注入策略：**

- 每轮向 Hot Layer 注入：当前活跃决策节点摘要（≤500 tokens）
- 状态转换时自动老化：不再相关的决策降级到 Warm Layer
- MemoryLookup 增强：不仅搜索 Memo，还同步搜索决策图 + 向量库
- 上下文溢出时：保留决策节点 + 最近 3 轮对话，其余压缩到 Warm Layer

### 4.2 编排调度设计

```
用户请求
    │
    ▼
┌─────────────────────────────────────┐
│  Hermes Orchestrator                │
│  (delegate_task 引擎)               │
│                                     │
│  1. 任务分解 (Plan Agent)            │
│  2. 任务调度 (DAG 依赖分析)          │
│  3. 执行派发 (Worker Agents)         │
│  4. 结果审查 (Reviewer Agent)        │
│  5. 冲突检测 + 决策图更新            │
└─────────────────────────────────────┘
    │
    ├── Scream 状态机: 记录当前状态
    ├── 上下文管理层: 更新决策图 + 向量库
    └── 用户: 状态报告
```

**关键流程变更：**

1. 每个子 Agent 执行前，从上下文管理层获取相关决策节点注入 prompt
2. 每个子 Agent 执行后，将新决策/发现写回上下文管理层
3. Scream 状态机仅作状态追踪，不做推理
4. 冲突检测（多 agent 写同一文件）在调度层完成，阻塞冲突任务

### 4.3 记忆系统的统一设计

```
┌────────────────────────────────────────────┐
│              统一记忆访问层                   │
│                                             │
│  MemoryLookup(query, scope)                 │
│    ├── 1. Scream SQLite (结构化经验)        │
│    ├── 2. LanceDB 向量库 (语义检索)         │
│    ├── 3. 决策节点图 (因果推理)             │
│    └── 4. 排序融合 (RRF 融合算法)           │
│                                             │
│  MemoryWrite(memo)                          │
│    ├── 1. Scream SQLite (保存结构化)        │
│    ├── 2. LanceDB (生成嵌入向量)            │
│    └── 3. 决策图 (如适用)                   │
│                                             │
│  Dream Consolidation                        │
│    ├── Scream 原有 /dream 逻辑             │
│    ├── + 向量去重 (余弦相似度 > 0.95)       │
│    └── + 决策图剪枝 (superseded 节点清理)   │
└────────────────────────────────────────────┘
```

### 4.4 与 Claude Code 的集成策略

Claude Code 是闭源的，所以集成方式是**API 层**而非代码层：

- Claude Code 作为独立的 CLI 进程运行
- 通过 MCP bridge 与 Orchestrator 通信
- Scream 配置中增加 Claude 作为可选 provider
- 任务路由策略：
  - **复杂推理/重构** → Claude (需要深度理解)
  - **常规编码/批量任务** → GPT-4o / DeepSeek (性价比)
  - **中文文档/文案** → 通义千问 (中文最优)

---

## 5. 实施路径

### Phase 1：基础桥接（2-4 周）

1. **搭建 MCP Bridge** 连接 Scream 和 Codex 沙箱
   - 实现 Codex sandbox-runner 的 MCP wrapper
   - Scream 的 Bash 工具请求路由到 sandbox-runner

2. **Hermes ↔ Scream 通信**（通过 MCP）
   - Hermes MCP server 暴露 delegate_task 能力
   - Scream 通过 MCP client 调用 Hermes 子 Agent 编排

3. **统一模型网关**
   - Scream 的 multi-provider 配置已具备
   - 增加 Claude 和 GPT-4o provider
   - 实现任务→模型路由策略

### Phase 2：上下文管理层（4-6 周）

1. **实现层级化上下文管理器**
   - Hot/Warm/Cold 三层
   - 滑动窗口 + LLM 摘要 + 向量检索

2. **因果记忆图**
   - 决策节点数据模型
   - 自动提取（从对话中识别决策/事实/拒绝）
   - 注入策略（Hot Layer 注入活跃节点）

3. **统一记忆访问层**
   - 集成 Scream SQLite + LanceDB
   - RRF 融合排序
   - MemoryLookup 增强

4. **Dream 增强**
   - 向量去重
   - 决策图剪枝

### Phase 3：编排优化（3-4 周）

1. **Hermes 编排器集成**
   - 1托2/DAG 模式标准化
   - 冲突检测机制
   - 决策节点自动写回

2. **Codex 插件市场接入**
   - 通过 MCP bridge 使用浏览器、LaTeX 等插件
   - 插件安装与管理

3. **Claude Code 集成**
   - 任务路由策略实施
   - 成本优化（简单任务不浪费 Claude）

### Phase 4：打磨与自适应（持续）

1. **Session Resume 增强** — 利用决策图快速恢复上下文
2. **AgingBench 自测** — 用学术界 benchmark 检验长项目能力
3. **遗忘曲线优化** — 基于时间 + 重要性的自动老化策略
4. **性能基准** — 建立 compaction 延迟、检索延迟等指标

---

## 6. 风险评估与取舍

### 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| MCP 协议仍在演进，可能 break | 中 | 高 | 版本协商 + 抽象适配层 |
| LanceDB 在 Windows 上兼容性问题 | 中 | 中 | SQLite-vec 作为备选 |
| Claude Code 闭源，集成深度受限 | 高 | 中 | API 层集成已够用，不依赖内部 |
| Hermes delegate_task 在超大项目中的性能 | 中 | 中 | 限制并行度，做好超时处理 |
| 上下文管理层复杂度高，难以调试 | 高 | 高 | 从 Phase 1 开始建立 observability |
| 四个系统的版本同步问题 | 中 | 低 | 每个系统独立版本，通过 MCP 契约解耦 |
| 用户学习曲线陡峭 | 中 | 中 | 渐进式引入，保留 Pure Scream 模式备选 |

### 关键的取舍决策

| 取舍 | 选择 | 理由 |
|------|------|------|
| 上下文管理层自建 vs 用现成框架 | **自建** | 没有一个现成框架满足 coding agent 场景 |
| 向量库 LanceDB vs Pinecone vs Chroma | **LanceDB** | 本地优先、零依赖、性能好、格式开放 |
| 编排器 Hermes vs 自建 | **Hermes** | 最成熟的子 Agent 系统，复用价值高 |
| 沙箱 Codex vs 自建 | **Codex** | 唯一有严谨沙箱的，Rust 性能好 |
| 状态机 Scream 做大脑 vs 做记录仪 | **记录仪** | 正如讨论结论，Scream 不适合做决策层大脑 |
| 推理模型单一种 vs 多种路由 | **多种路由** | 按任务匹配模型，成本优化 |

### 不做的事（明确排除的范围）

1. **不修改 Claude Code 核心** — 闭源，只做 API 层集成
2. **不重写 Scream 核心** — 用其框架能力，只补充上下文管理层
3. **不自研沙箱** — 直接复用 Codex sandbox-runner
4. **不做 1:N 模型替代** — 让用户自己通过 Scream 配置选择
5. **不做统一 UI** — 保留 Scream TUI / Hermes Gateway / CLI 各自的交互

---

## 附录：调研数据源

### 本地源码分析
- Scream Code: `C:/Users/Administrator/AppData/Roaming/npm/node_modules/scream-code/dist/` (bundled JS)
- Codex CLI: `.codex/.sandbox-bin/` 目录结构确认
- Hermes: `.hermes/hermes-agent/` (完整 Python 源码 + AGENTS.md + config.yaml)
- 配置文件: `.scream-code/config.toml`, `.hermes/config.yaml`

### Web 调研
- Anthropic官方文档: `docs.anthropic.com/en/docs/claude-code/`
- Scream Code GitHub: `github.com/LIUTod/scream-code`
- Codex CLI GitHub: `github.com/openai/codex`
- Nous Research Hermes: `github.com/nousresearch/hermes-agent`
- 学术论文: AgingBench(2025), ContextWeaver(2025), MemGPT, GitOfThoughts(2025)
- 框架文档: LangGraph, CrewAI, AutoGen, MCP 协议规范

---

*本文档基于 2026 年 6 月 19 日的调研结果编写。各系统版本可能更新，建议实施前重新确认。*
