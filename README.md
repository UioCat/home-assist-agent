# Home Assist Agent

家庭生活助理项目。目标是先做出本地可部署、用户本人能长期自用的软件，再逐步扩展到家庭多人、多入口和客户展示场景。系统通过基于 Codex 的 Agent 完成理解、计划、建议、确认和受控执行。

本项目不是 Home Assistant 的替代品。Home Assistant 是设备、实体、服务和实时状态的事实源；本项目负责身份、权限、安全边界、记忆、任务、确认、审计、通知，以及通过 Codex SDK 调用 Codex。

## 当前可运行 MVP

仓库已经包含一个本地、无鉴权的最小 Web 产品：

- React 单页工作台：输入指令，查看固定路由策略、依赖状态和执行轨迹。
- Python/FastAPI 后端：统一提供指令与健康接口，并直接托管 React 生产构建。
- 三类分发：Codex 以 `low` 判断直接控制、模糊控制或普通请求；模糊控制由 `medium` 规划设备工具；普通请求由 `high` 回答。
- Home Assistant MCP：设备控制分支执行前读取实时工具定义，工具名和参数都经过安全策略与 JSON Schema 校验。
- 本地 Codex 封装：使用只读、无审批、临时会话运行，并隔离用户全局 Codex 配置。
- 事件入口：接收人员进入、就座等外部事件，按来源事件 ID 去重并更新家庭上下文；默认无规则时不调用 Codex 或 HA。
- SQLite 追加式审计：使用同一个 `message_id` 串联用户、事件、Codex 和 Home Assistant MCP 的完整请求与响应，并通过 `correlation_id` / `causation_id` 关联场景与因果链。

当前 MVP 仅允许开、关、灯光设置和实时上下文类工具，并阻止门锁、车库门、燃气、供水和摄像头等高风险目标。一次请求最多执行一个 MCP 工具。

### 前置条件

- Python 3.11+
- Node.js/npm
- 已安装并登录的 Codex CLI，可用 `codex login status` 检查
- 启用了 MCP Server 集成的 Home Assistant
- 一个 Home Assistant 长期访问令牌

### 配置与启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

cp .env.example .env
# 编辑 .env，至少填写 HA_MCP_URL 和 HA_TOKEN

cd frontend
npm install
npm run build
cd ..

.venv/bin/python -m home_assist_agent
```

浏览器打开 `http://127.0.0.1:8080` 使用指令中心，打开 `http://127.0.0.1:8080/audit` 查看按 `message_id` 串联的审计中心。默认配置不会监听外网地址，也没有鉴权；不要把该端口直接暴露到局域网或公网。

开发前端时可运行 `cd frontend && npm run dev`，Vite 会把 `/api` 转发到 `127.0.0.1:8080`。

### 分发行为

| 类型 | 示例 | 处理路径 |
| --- | --- | --- |
| 直接 IoT | `打开客厅灯`、`把客厅灯调到 30%` | Codex `low` 路由并输出设备指令 → HA MCP |
| 间接 IoT | `客厅太暗了` | Codex `low` 路由 → HA 实时安全工具 → Codex `medium` 设备规划 → HA MCP |
| 其余指令 | `介绍一下你能做什么` | Codex `low` 路由 → Codex `high` 普通回答；不访问 HA |

主要接口：

- `POST /api/commands`：提交 `{"command": "..."}`；兼容字段 `reasoning` 会被记录，但不影响固定执行等级
- `POST /api/events`：接收事件并返回 `observed|duplicate|triggered`
- `GET /api/health`：检查后端、Codex 登录状态和 HA MCP 连接状态
- `GET /api/audit`：按时间倒序查询消息审计摘要
- `GET /api/audit/{message_id}`：按事件顺序查询一条消息的完整审计链路

`POST /api/commands` 可选传入不超过 128 个字符的 `message_id`；未传入时由后端生成。响应中的 `message_id` 和兼容字段 `request_id` 使用同一个值。审计事件默认写入 `data/audit.db`，可通过 `AUDIT_DB_PATH` 修改。

事件的 `source + event_id` 是幂等键；稳定 `message_id` 会贯穿接收、上下文、规则匹配以及可能的 Codex/HA 调用。人员位置等最新事实默认写入 `data/events.db`，可通过 `EVENT_DB_PATH` 修改。当前规则引擎默认为空，因此事件只会被记录并更新上下文，不会自主控制设备。

运行测试：

```bash
.venv/bin/pytest
cd frontend && npm test -- --run
```

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
