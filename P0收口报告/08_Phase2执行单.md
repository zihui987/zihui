# Phase 2 配置执行单 + 结论

> 日期：2026-06-20 | 前置依赖：Phase 1 ✅ | GitHub 备份 ✅

---

## 一、执行边界

- 仅做 Phase 2 三项收口
- 不恢复正式内容生产
- 不新增业务任务
- 不顺带改规则层无关内容
- 不扩大到 Phase 3

---

## 二、Item 1：orchestrator_profile 收口

### 变更

| 配置项 | 变更前 | 变更后 |
|--------|--------|--------|
| `kanban.orchestrator_profile` | `''`（空） | `xiaxia` |

### 含义

| 问题 | 答案 |
|------|------|
| 调度入口是谁？ | **xiaxia**（唯一） |
| 是否允许第二调度入口？ | **禁止** |
| xiaxia 能否绕过 orchestrator 直接操作？ | 可以，xiaxia 同时是 orchestrator 和主执行者 |
| 与之前"夏夏手动编排"有何不同？ | 之前 orchestrator_profile 为空，kanban 自动派发时无人负责编排流转。设定后，xiaxia（夏夏）成为 kanban 编排者，负责任务分发、状态流转、审核路由的调度 |

### 验证标准

- `grep orchestrator_profile .hermes/config.yaml` → `orchestrator_profile: xiaxia`

---

## 三、Item 2：MCP / toolset 权限收口

### 现状（Phase 1 完成后）

| Tool | xiaxia（编排者） | xiaren（执行岗） | xiashen（审核岗） |
|------|:---:|:---:|:---:|
| file | ✅ | ✅ | ✅ |
| terminal | ✅ | ✅ | ❌ |
| search | ✅ | ✅ | ✅ |
| delegation | ✅ | ✅ | ✅ |
| todo | ✅ | ✅ | ✅ |
| mcp | ❌（由主 session 继承） | ❌ | ❌ |
| memory | ✅（主 session 默认） | ❌ | ❌ |

### 岗位工具权责

| 岗位 | 必备工具 | 禁止调用的工具 | 说明 |
|------|----------|---------------|------|
| **xiaxia**（编排者） | file, terminal, search, delegation, todo | —（全权限） | 编排者持有全部工具权限 |
| **xiaren**（执行岗） | file, terminal, search, delegation, todo | mcp, memory | 执行岗不应直接调用 MCP 服务或修改全局记忆 |
| **xiashen**（审核岗） | file, search, delegation, todo | **terminal**, mcp, memory | 审核岗不应写文件系统，不应改内容 |

### 关键约束说明

1. **xiashen 无 terminal**：审核岗只能读取、查看、比对，不能执行写操作。如果审核报告需要生成文件，应通过 file tool 而非 terminal
2. **三岗位均无 mcp**：MCP 工具的调用由主 Hermes session（夏夏）统一控制，profile 不直接获取 MCP 权限。这是防止越权的关键屏障
3. **delegation**：所有岗位均可使用 delegation 进行子任务分派，但子任务会继承岗位本身的工具权限

### 当前差异确认

与 Phase 2 目标相比，当前配置**已基本对齐**，无需额外修改 toolsets 配置：

| Profile | 是否对齐 | 备注 |
|---------|---------|------|
| xiaxia | ✅ | 持有全部工具，无需变更 |
| xiaren | ✅ | 持有执行所需全部工具，已限制 mcp/memory |
| xiashen | ✅ | 无 terminal → 不能写文件系统，已限制 mcp/memory |

**无需修改各 profile 的 toolsets 配置。**

---

## 四、Item 3：审核路由收口

### 完整流转图

```
xiaxia（编排者）
  │ 创建任务（kanban.create）
  │ 设 assignee = xiaren
  ▼
xiaren（执行岗）
  │ 接收任务 → 执行 → 交付物落盘
  │ 完成后通知 xiaxia
  │ xiaxia 更新 kanban 状态 → review
  ▼
[人工确认点 ①] xiaxia 判断：直接归档？还是送审？
  │ 若送审 → 设 assignee = xiashen
  ▼
xiashen（审核岗）
  │ 接收审核任务 → 审查交付物
  │ 输出审核结论（PASS / RETURN）
  │ 通知 xiaxia
  ▼
[人工确认点 ②] xiaxia 判断审核结论：
  │ PASS    → 更新为 completed → 归档
  │ RETURN  → 退回 xiaren 重新执行
  ▼
完成
```

### 状态推进责任

| 状态 | 推进者 | 自动化程度 | 说明 |
|------|--------|-----------|------|
| created | xiaxia | 手动（编排者创建） | 编排者决策任务内容 |
| → assigned（xiaren） | kanban dispatch | **自动** | 60s 间隔，orchestrator_profile 已设为 xiaxia |
| → in_progress | xiaren 自动认领 | **自动** | profile 接收后自动开始 |
| → review | xiaxia | **手动** | 执行完成后需编排者确认是否送审 |
| → in_review（xiashen） | kanban dispatch | **自动** | xiaxia 设置审核 assignee 后自动派发 |
| → PASS / RETURN | xiashen | **自动**（审核结论） | xiashen 输出结论后通知 xiaxia |
| → completed | xiaxia | **手动** | 编排者最终确认 |
| → archived | kanban | **自动** | completed 超时后自动归档 |

### 必须人工确认的节点

| 节点 | 原因 | 禁止自动跳过 |
|------|------|-------------|
| **任务创建** | 需要编排者判断任务类型、优先级、排期 | ✅ |
| **执行完成 → 送审判断** | 编排者需判断是否需审核，还是可以直接归档 | ✅ |
| **审核结论裁决** | PASS 或 RETURN 由编排者最终裁定（审核岗可建议，不决策） | ✅ |

### 允许自动流转的节点

| 节点 | 条件 |
|------|------|
| 任务派发至 xiaren | kanban dispatch 60s 间隔自动执行 |
| 审核任务派发至 xiashen | xiaxia 设置 assignee = xiashen 后自动派发 |
| 审核结论通知 | xiashen 返回结论后自动通知 xiaxia |
| 归档 | completed 状态自动触发归档 |

### 审核路由规则总结

| 规则 | 内容 |
|------|------|
| 谁决定是否送审？ | xiaxia（编排者） |
| 谁执行审核？ | xiashen（审核岗） |
| 谁裁决审核结论？ | xiaxia（编排者） |
| 执行→审核是否自动？ | 否（需 xiaxia 人工判断） |
| 审核→回退是否自动？ | 否（需 xiaxia 人工裁决） |
| 审核→完成是否自动？ | 否（需 xiaxia 人工确认） |

---

## 五、配置变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `.hermes/config.yaml` line 501 | `orchestrator_profile: ''` → `xiaxia` | Item 1 唯一变更 |
| `.hermes/profiles/xiaren/config.yaml` | 无变更 | Phase 1 已添加 delegation |
| `.hermes/profiles/xiashen/config.yaml` | 无变更 | Phase 1 已添加 delegation |

**Phase 2 实际仅 1 处配置变更。**

---

## 六、验证结果

| 验证项 | 结果 | 方式 |
|--------|------|------|
| orchestrator_profile 已设 | ✅ | grep config.yaml |
| xiaren toolsets 合规 | ✅ | Read 确认 |
| xiashen toolsets 合规（无 terminal） | ✅ | Read 确认 |
| 审核路由规则已定义 | ✅ | 本文件 §4 |
| 人工确认点已定义 | ✅ | 本文件 §4 |

---

## 七、是否具备恢复正式生产的结论

### 已满足条件

| 条件 | 状态 |
|------|------|
| 任务系统已定版（kanban 为主） | ✅ |
| 网关收口已验证 | ✅ |
| Phase 1 执行完成（delegation + task_flow 写关闭） | ✅ |
| GitHub 备份已完成 | ✅ |
| orchestrator_profile 已设 | ✅ |
| toolset 权限已收口 | ✅ |
| 审核路由规则已定义 | ✅ |
| 人工确认点已明确 | ✅ |

### 仍存在的限制

| 限制 | 影响 | 建议 |
|------|------|------|
| 审核路由依赖 xiaxia 手动送审 | 非自动，需编排者始终在线 | 可接受，人工确认点安全性高于自动 |
| 无自动归档触发 | completed 后需 xiaxia 主动归档 | 低影响，习惯后可为 |
| Phase 3 未执行（task_flow 数据迁移、退役评估） | 历史生产数据仍在 task_flow.db | 建议后续补充，但不阻塞恢复生产 |
| 自动审核结论裁决未实现 | PASS/RETURN 由编排者决策 | 正确做法，不应由审核岗自行决定 |

### 最终结论

**已具备恢复正式生产的条件。**

以上限制均为设计决策（人工确认点保留），非未完成任务。建议以 **有监督模式** 恢复生产——即 xiaxia 作为编排者全程参与前三轮生产循环，确认自动流转节点稳定后，再逐步放开。

---

*签署栏*

| 角色 | 签字 | 日期 |
|------|------|------|
| 执行人（我） | ✅ 已执行 | 2026-06-20 |
| 决策人（你） | ________ | 2026-06-20 |

---

*文件路径：`C:\Users\Administrator\P0收口报告\08_Phase2执行单.md`*
