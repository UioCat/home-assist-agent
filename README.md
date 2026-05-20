# Home Assist Agent

家庭生活助理项目。目标是先做出本地可部署、用户本人能长期自用的软件，再逐步扩展到家庭多人、多入口和客户展示场景。系统通过基于 Codex 的 Agent 完成理解、计划、建议、确认和受控执行。

本项目不是 Home Assistant 的替代品。Home Assistant 是设备、实体、服务和实时状态的事实源；本项目负责身份、权限、安全边界、记忆、任务、确认、审计、通知，以及通过 Codex SDK 调用 Codex。

## 核心定位

- 第一期（本地可用版）：单家庭、单 owner、本地 PWA、真实 HA 低风险控制、提醒、记忆和解释型审计。
- 展示增强：demo mode、决策卡片、trace replay、mock 门锁/camera/OCR 注入，服务客户 3 分钟展示。
- 长期多入口：微信、钉钉、飞书、家庭麦克风、IoT 事件、camera 事件、定时任务。
- 长期多用户：每个自然人有独立身份、权限、记忆、偏好、会话和 Codex 工作目录。
- 家庭共享上下文：家庭规则、共享习惯、共同任务和跨用户冲突策略独立于个人记忆。
- Codex 推理：Codex 负责理解意图、拆解任务、生成解释和调用受控工具。
- 外层控制：身份、权限、上下文装配、确认、审计、长期记忆写入、任务恢复和通知投递由本项目控制。
- HA 事实源：设备能力和实时状态由 Home Assistant 维护，本项目第一期不做独立 `Capability Registry`，不维护完整 `Home State`。

## 架构主线

```text
Adapters
  -> Relay / Event Log
  -> Application Orchestrator
  -> Identity / Policy / Context
  -> Codex SDK Runner
  -> Tool Safety Proxy
  -> HA Adapter
  -> Home Assistant MCP
  -> Notification Policy
  -> Message Router / Screen Policy
```

关键不变量：

- 任何真实世界副作用都不得绕过 `Tool Safety Proxy`。
- Codex 可以提出动作、任务、确认请求和记忆候选，但不能直接拥有最终执行权。
- Codex 不直接写长期记忆，只能提交 `MemoryCandidate`。
- 确认是可恢复任务，必须可过期、可撤销、可审计。
- 输出目标由 `Notification Policy` 决定，Codex 不直接决定发给谁。
- 审计日志是跨模块 append-only ledger，不是普通输出日志。

## 文档索引

详细设计已拆分到 `docs/`：

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 所有设计文档的阅读顺序和维护说明 |
| [第一期（本地可用版）](docs/phase-1-local-usable.md) | 第一期目标、范围、可降档能力、验收标准和展示增强边界 |
| [本地单用户模式](docs/local-single-user-mode.md) | 单 owner 本地部署下的安全降档方式和不可降边界 |
| [Showcase 场景](docs/showcase-scenarios.md) | demo mode、客户演示脚本、决策卡片和 trace replay |
| [本地运维手册](docs/local-operations-runbook.md) | 安装、配置、健康检查、备份恢复、降级和安全急停 |
| [总体架构](docs/architecture.md) | 分层架构、架构不变量、核心契约、主请求链路、第一期边界 |
| [输入安全](docs/input-security.md) | 输入适配、统一消息、信任分层、ContextBlock、prompt injection、群聊/ASR/OCR/camera 安全 |
| [身份与权限](docs/identity-permissions.md) | 多用户身份、HomeMembership、身份合并/解绑/撤销、RBAC + ABAC |
| [记忆、上下文与会话](docs/memory-context-sessions.md) | MemoryCandidate、MemoryEntry、SessionSummary、48 小时压缩、上下文装配 |
| [任务与确认](docs/tasks-confirmations.md) | Task Orchestrator、TaskRun、ActionPlan、Confirmation Broker |
| [工具安全与 HA 边界](docs/tool-safety-ha.md) | Tool Safety Proxy、HAAdapter、HA MCP、幂等、target 展开、HA 返回语义 |
| [消息路由与原渠道响应](docs/message-routing.md) | RouteRef、ReplyTarget、MessageEnvelope、DeliveryAttempt、原渠道回复和 fallback |
| [日志、审计与可观测性](docs/logging-observability.md) | EventLog、AuditEvent、TraceSpan、RuntimeLog、MetricsRollup、按人/模块查询 |
| [可视化触摸屏与家庭屏幕](docs/visual-surfaces.md) | Pad、墙面屏、DisplaySurface、ScreenSession、Screen Policy、公共屏隐私边界 |
| [通知、审计与运行保障](docs/notifications-audit-operations.md) | Notification Policy、Audit Log、可观测性、降级恢复、工程结构、第一期顺序 |
