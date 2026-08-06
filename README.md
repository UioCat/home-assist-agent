# Home Assist Agent

家庭生活助理项目。目标是先做出本地可部署、用户本人能长期自用的软件，再逐步扩展到家庭多人、多入口和客户展示场景。系统通过基于 Codex 的 Agent 完成理解、计划、建议、确认和受控执行。

本项目不是 Home Assistant 的替代品。Home Assistant 是设备、实体、服务和实时状态的事实源；本项目负责身份、权限、安全边界、记忆、任务、确认、审计、通知，以及通过 Codex SDK 调用 Codex。

## 当前可运行 MVP

仓库已经包含一个本地、无鉴权的最小 Web 产品：

- 统一 React 控制台：同一侧栏内使用家庭指令、主流程审计、设备实例、物模型、Provider 和操作事件页面。
- 两个独立 FastAPI 后端：主流程接口使用 `8080`，IoT MCP 接口使用 `8090`，彼此不进行后端调用。
- 三类分发：Codex 以 `low` 判断直接控制、模糊控制或普通请求；设备目标由确定性候选集与 Codex `medium` 排序解析；模糊控制再由 `medium` 规划非目标参数；普通请求由 `high` 回答。
- 受限语义目标解析：从 HA 实时状态和实体、设备、区域注册表读取稳定 `entity_id`，优先使用精确名称、别名和个人术语；精确匹配缺失时，从动作兼容设备生成最多 20 个语义后备候选。Codex 只能选择候选编号，执行前会重新读取目录并确定性校验。
- 混合自治澄清：唯一高置信度目标可直接执行；多个合理目标会点名真实设备询问。用户可选择单个设备或确认“全部”，系统只控制本次展示的候选并在完整成功后学习该称呼。
- 个人术语学习：整个实体集合执行成功后默认静默创建个人 `provisional` 术语，10 分钟无纠正自动批准；“不是这个”可撤销，“全家都这么叫”必须再确认后才共享。
- Home Assistant MCP：设备控制分支执行前读取实时工具定义，工具名和参数都经过安全策略与 JSON Schema 校验。
- 本地 Codex 封装：使用只读、无审批、临时会话运行，并隔离用户全局 Codex 配置。
- 事件入口：接收人员进入、就座等外部事件，按来源事件 ID 去重并更新家庭上下文；默认无规则时不调用 Codex 或 HA。
- SQLite 追加式审计：使用同一个 `message_id` 串联用户、事件、Codex 和 Home Assistant MCP 的完整请求与响应，并通过 `correlation_id` / `causation_id` 关联场景与因果链。

当前 MVP 仅允许开、关、灯光设置和实时上下文类工具，并继续使用现有兼容安全策略。一个已验证候选最多包含 20 个实体；集合按 `entity_id` 稳定顺序逐项执行，首个失败后停止，且部分成功不会学习术语。

### 前置条件

- Python 3.11+
- Node.js/npm
- [uv](https://docs.astral.sh/uv/)
- 已安装并登录的 Codex CLI，可用 `codex login status` 检查
- 启用了 MCP Server 集成的 Home Assistant
- 一个 Home Assistant 长期访问令牌

### 配置与启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

cp .env.example .env
# 编辑 .env，至少填写 HA_BASE_URL、HA_MCP_URL 和 HA_TOKEN

cd modules/iot-mcp/backend
uv sync --extra dev

cd ../web
npm ci
cd ../../..

.venv/bin/python scripts/dev_all.py
```

浏览器统一打开 `http://127.0.0.1:5173`。启动器会同时运行：

| 进程 | 地址 | 用途 |
| --- | --- | --- |
| 统一前端 | `http://127.0.0.1:5173` | 本地开发入口 |
| Home Assist Agent | `http://127.0.0.1:8080` | 指令、健康和主流程审计 API |
| IoT MCP | `http://127.0.0.1:8090` | 设备 API、OpenAPI 和生产构建页面 |

三个进程各自监听独立端口。按 `Ctrl+C` 会完整停止全部进程；任一子进程异常退出时，启动器也会清理其余子进程。运行 `.venv/bin/python scripts/dev_all.py --describe` 可只查看入口，不会输出 HA Token 或其他凭据。

如果本机 `5173` 已被其他项目占用，只移动前端端口即可：`.venv/bin/python scripts/dev_all.py --frontend-port 5174`。两个后端仍固定使用 `8080` 和 `8090`。

默认配置只监听本机地址，IoT Web 暂时关闭鉴权；不要把这些端口直接暴露到局域网或公网。根后端默认仅提供 API，不再托管旧 `frontend` 构建。

统一前端的 Vite 会把 `/agent-api/*` 转发到 `8080/api/*`，把 `/api/v1/*` 转发到 `8090/api/v1/*`，两个后端仍互不依赖。

生产构建使用：

```bash
cd modules/iot-mcp/web
npm run build
cd ../backend
uv run python -m iot_mcp --mode http
```

随后从 `http://127.0.0.1:8090` 打开统一页面；主流程后端仍需在 `8080` 单独运行。

### 分发行为

| 类型 | 示例 | 处理路径 |
| --- | --- | --- |
| 直接 IoT | `打开客厅灯`、`把客厅灯调到 30%` | Codex `low` 路由 → HA 目录与个人术语生成精确或语义后备候选 → Codex `medium` 选择、判歧义或拒绝 → 必要时定向澄清 → 刷新校验 → HA MCP |
| 间接 IoT | `客厅太暗了` | Codex `low` 路由 → 候选解析与刷新校验 → Codex `medium` 只规划非目标参数 → HA MCP |
| 其余指令 | `介绍一下你能做什么` | Codex `low` 路由 → Codex `high` 普通回答；不访问 HA |

主要接口：

- `POST /api/commands`：提交 `{"command": "..."}`；兼容字段 `reasoning` 会被记录，但不影响固定执行等级
- `POST /api/events`：接收事件并返回 `observed|duplicate|triggered`
- `GET /api/health`：检查后端、Codex 登录状态和 HA MCP 连接状态
- `GET /api/audit`：按时间倒序查询消息审计摘要
- `GET /api/audit/{message_id}`：按事件顺序查询一条消息的完整审计链路

`POST /api/commands` 可选传入不超过 128 个字符的 `message_id`；未传入时由后端生成。响应中的 `message_id` 和兼容字段 `request_id` 使用同一个值。审计事件默认写入 `data/audit.db`，可通过 `AUDIT_DB_PATH` 修改。

目标解析默认启用，可通过以下配置调整：

```text
HA_BASE_URL=http://homeassistant.local:8123
HOME_ID=local-home
PERSON_ID=local-user
TARGET_RESOLUTION_ENABLED=true
TARGET_RESOLUTION_CONFIDENCE=0.80
TARGET_CANDIDATE_LIMIT=20
TERM_PROVISIONAL_SECONDS=600
TERM_DB_PATH=data/terms.db
```

`HA_BASE_URL` 用于 HA 原生 REST/WebSocket 目录读取，不会从 `HA_MCP_URL` 隐式推导。`HOME_ID` 和 `PERSON_ID` 由本地可信通道注入，公开命令 API 不接受浏览器提交这两个身份字段。术语库使用 SQLite WAL 和 `0600` 文件权限，保留不可更新、不可删除的状态修订。迁移排障时可以临时设置 `TARGET_RESOLUTION_ENABLED=false` 回到旧兼容路径，但该路径不会提供候选约束和术语学习。

事件的 `source + event_id` 是幂等键；稳定 `message_id` 会贯穿接收、上下文、规则匹配以及可能的 Codex/HA 调用。人员位置等最新事实默认写入 `data/events.db`，可通过 `EVENT_DB_PATH` 修改。当前规则引擎默认为空，因此事件只会被记录并更新上下文，不会自主控制设备。

运行测试：

```bash
.venv/bin/pytest
cd modules/iot-mcp/backend && uv run pytest
cd ../web && npm test -- --run && npm run typecheck && npm run build
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
