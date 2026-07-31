# IoT MCP 子模块实施计划

设计依据：`docs/superpowers/specs/2026-07-29-iot-mcp-submodule-design.md`

## Global Constraints

- 所有实现位于 `modules/iot-mcp/`，不得改动主项目现有业务调用链。
- Backend 使用 Python 3.11+、FastAPI、Pydantic、SQLAlchemy async、SQLite、httpx、WebSocket 和官方 Python MCP SDK。
- Web 使用 React、TypeScript 和 Vite；页面必须可实际查询和控制设备，不能只做静态样例。
- Home Assistant 直接使用 REST API 与 WebSocket API；禁止通过 `POST /api/states/{entity_id}` 控制真实设备。
- TSL JSON 保持阿里云 `schema/profile/properties/services/events` 标准结构；Provider 绑定单独存储。
- Provider 是设备实时状态事实源；本地快照必须包含 `observed_at`，不得成为执行事实源。
- Web 已认证用户主动控制固定标记为 `human_interactive`，高危操作直接执行，不二次确认。
- MCP、后台 Token、Scheduler 和事件联动固定标记为 `autonomous`；自动高危操作只能创建确认请求。
- MCP 不得提供批准确认工具；确认只能来自已认证 Web 路由或签名消息回调。
- 调用方不得通过请求体声明或提升 `interaction_mode`。
- 所有写操作先持久化 `ControlOperation`，使用幂等键，并记录 Provider 请求和结果。
- SQLite 或审计不可写时阻断设备写操作；只读查询和诊断可以继续。
- 非 HA 设备只实现 `LanHttpDeviceProvider` 契约和 Mock，不接真实厂商协议。
- 测试不得依赖真实 HA Token；HA 行为使用 MockTransport、假 WebSocket 或确定性 fixture。
- 每个 Task 必须运行所列精确测试并提交；不得提交 `.venv/`、`node_modules/`、构建缓存、数据库或 Secret。

## Task 1: Backend scaffold, TSL domain, and persistence

### Goal

建立可安装、可测试的 Backend 工程，完成 TSL 领域模型、设备/操作/确认领域对象和 SQLite async 仓储。

### Files

- Create: `modules/iot-mcp/backend/pyproject.toml`
- Create: `modules/iot-mcp/backend/src/iot_mcp/__init__.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/domain/enums.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/domain/tsl.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/domain/models.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/config/settings.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/persistence/database.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/persistence/tables.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/persistence/repositories.py`
- Create: `modules/iot-mcp/backend/tests/unit/test_tsl.py`
- Create: `modules/iot-mcp/backend/tests/integration/test_repositories.py`
- Create: `modules/iot-mcp/.gitignore`

### Requirements

- `pyproject.toml` 定义 runtime 和 `dev` extra，支持 `uv sync --extra dev`。
- TSL 支持 Property、Service、Event 以及 `int/float/double/text/date/bool/enum/struct/array` 数据类型。
- TSL 校验必须覆盖 identifier 唯一性、`accessMode`、类型与 specs、服务输入、必填字段和属性写入值。
- 领域对象至少包含 `ThingProduct`、`ThingModelVersion`、`DeviceInstance`、`ProviderDeviceBinding`、`FeatureBinding`、`PropertySnapshot`、`DeviceEvent`、`ControlOperation`、`ConfirmationRequest`。
- 操作状态覆盖 `requested/pending_confirmation/approved/executing/succeeded/no_op/accepted/failed/unknown/rejected/expired`。
- SQLAlchemy 表与仓储支持产品/模型、设备/绑定、快照/事件、操作/确认和幂等查询。
- 数据库初始化开启 SQLite WAL 和 foreign keys。
- 时间统一存储 UTC aware datetime；JSON 字段保留结构，不以自由文本替代。

### Tests

Run:

```bash
cd modules/iot-mcp/backend
uv sync --extra dev
uv run pytest tests/unit/test_tsl.py tests/integration/test_repositories.py -q
uv run ruff check src tests
```

### Commit

```text
feat(iot-mcp): add domain and persistence foundation
```

## Task 2: Provider port, Mock provider, HA provider, and sync

### Goal

实现稳定 DeviceProvider Port、确定性 Mock Provider、Home Assistant REST/WebSocket 适配和设备同步服务。

### Files

- Create: `modules/iot-mcp/backend/src/iot_mcp/ports/device_provider.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/mock/provider.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/home_assistant/client.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/home_assistant/mapping.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/home_assistant/provider.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/application/sync_service.py`
- Create: `modules/iot-mcp/backend/tests/contract/test_device_provider_contract.py`
- Create: `modules/iot-mcp/backend/tests/unit/test_ha_mapping.py`
- Create: `modules/iot-mcp/backend/tests/integration/test_ha_provider.py`

### Requirements

- Port 包含 `health/discover/read_state/write_properties/invoke_service/subscribe`。
- Mock Provider 提供可调光灯、空调和高危门锁，支持状态变化事件和故障注入。
- HA REST Client 使用 Bearer Token、JSON 超时和稳定错误分类。
- HA 控制只调用 `/api/services/{domain}/{service}`；写入前后读取状态。
- HA Mapping 规则：HA Device 对应 DeviceInstance；Entity 对应 TSL 功能；无 device_id 的 Entity 创建虚拟实例。
- 能力指纹不依赖显示名称和可变 entity_id；相同能力生成相同 product key。
- 覆盖 light、switch、climate、lock 的核心属性和服务映射，亮度完成 0–255 与 0–100 转换。
- Sync Service 完成全量发现、实例/绑定 upsert、missing 标记和属性快照刷新。
- WebSocket 订阅接口必须可注入假事件源测试，不依赖真实网络。

### Tests

Run:

```bash
cd modules/iot-mcp/backend
uv run pytest tests/contract/test_device_provider_contract.py tests/unit/test_ha_mapping.py tests/integration/test_ha_provider.py -q
uv run ruff check src tests
```

### Commit

```text
feat(iot-mcp): add home assistant provider and sync
```

## Task 3: Control orchestration, confirmation, webhook, and HTTP API

### Goal

实现统一查询/控制应用服务、来源安全边界、自动高危确认、签名 Webhook 和 FastAPI 主契约。

### Files

- Create: `modules/iot-mcp/backend/src/iot_mcp/ports/message_channel.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/application/query_service.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/application/policy.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/application/control_service.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/application/confirmation_service.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/outbound/webhook/channel.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/inbound/http/auth.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/inbound/http/schemas.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/inbound/http/dependencies.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/inbound/http/routes.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/inbound/http/app.py`
- Create: `modules/iot-mcp/backend/tests/unit/test_policy.py`
- Create: `modules/iot-mcp/backend/tests/unit/test_control_service.py`
- Create: `modules/iot-mcp/backend/tests/integration/test_http_api.py`
- Create: `modules/iot-mcp/backend/tests/integration/test_webhook_confirmation.py`

### Requirements

- HTTP 路由覆盖设计文档中的物模型、设备、状态、属性写入、服务调用、操作、确认、消息回调和 Provider 同步。
- Web 登录使用本地 Admin Token 换取短期、签名、HttpOnly Session Cookie，并为写请求校验 CSRF Token。
- 只有有效 Web Session + CSRF 的交互路由标记 `human_interactive`；直接携带 Admin Token 的 API 调用固定标记为 `autonomous`。
- 机器 Token、MCP 或缺失身份固定为 `autonomous`，请求体中的 mode/initiator 字段必须忽略或拒绝。
- 人工高危操作通过基础校验后直接执行，不创建 ConfirmationRequest。
- 自动高危操作创建 `pending_confirmation`，不调用 Provider；低风险自动操作可执行。
- 确认绑定 `action_hash`、授权用户、Provider binding revision 和过期时间。
- Webhook 使用 HMAC-SHA256，校验 timestamp、nonce、防重放、actor、decision、confirmation_id 和 action_hash。
- 幂等命中返回原 operation；Provider 超时返回 `unknown`；状态已满足返回 `no_op`。
- MCP 自动高危操作无法通过任何 HTTP/MCP 参数自行批准。
- API 错误返回稳定 code、message、retryable 和 request_id，不泄漏 Secret。

### Tests

Run:

```bash
cd modules/iot-mcp/backend
uv run pytest tests/unit/test_policy.py tests/unit/test_control_service.py tests/integration/test_http_api.py tests/integration/test_webhook_confirmation.py -q
uv run ruff check src tests
```

### Commit

```text
feat(iot-mcp): add safe control and http api
```

## Task 4: MCP server and runnable bootstrap

### Goal

实现官方 Python MCP SDK 工具面、应用装配、进程入口、健康启动和构建后 Web 静态资源托管。

### Files

- Create: `modules/iot-mcp/backend/src/iot_mcp/adapters/inbound/mcp/server.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/bootstrap/container.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/bootstrap/runtime.py`
- Create: `modules/iot-mcp/backend/src/iot_mcp/__main__.py`
- Create: `modules/iot-mcp/backend/tests/unit/test_mcp_tools.py`
- Create: `modules/iot-mcp/backend/tests/integration/test_runtime.py`

### Requirements

- MCP tools 精确包含 `list_thing_models/get_thing_model/list_devices/get_device/get_device_state/set_device_properties/invoke_device_service/get_operation/query_device_events`。
- MCP 不注册 approve/reject confirmation 工具。
- MCP 写工具在服务端固定创建 `autonomous` 请求；自动高危返回 `confirmation_required` 和 operation_id。
- Runtime 装配配置、数据库、仓储、Provider、Control Service、HTTP App 和 MCP Server。
- 启动时初始化数据库并执行 Provider 初次同步；同步失败时服务以 degraded 状态启动，不能伪造在线。
- 后端在 `web/dist` 存在时托管 SPA，并保留 `/api` 与 `/mcp` 路由。
- 提供可分别启动 HTTP 和 MCP 的 CLI 参数，默认配置不要求真实 HA Token，可使用 Mock Provider。

### Tests

Run:

```bash
cd modules/iot-mcp/backend
uv run pytest tests/unit/test_mcp_tools.py tests/integration/test_runtime.py -q
uv run pytest -q
uv run ruff check src tests
```

### Commit

```text
feat(iot-mcp): expose mcp tools and runtime
```

## Task 5: React management interface

### Goal

实现可操作的设备控制台，覆盖设计文档全部页面，并通过 HTTP API 完成数据读取、人工直控和确认。

### Files

- Create: `modules/iot-mcp/web/package.json`
- Create: `modules/iot-mcp/web/vite.config.ts`
- Create: `modules/iot-mcp/web/tsconfig.json`
- Create: `modules/iot-mcp/web/index.html`
- Create: `modules/iot-mcp/web/src/main.tsx`
- Create: `modules/iot-mcp/web/src/app.tsx`
- Create: `modules/iot-mcp/web/src/styles.css`
- Create: `modules/iot-mcp/web/src/api/client.ts`
- Create: `modules/iot-mcp/web/src/components/*`
- Create: `modules/iot-mcp/web/src/pages/*`
- Create: `modules/iot-mcp/web/src/test/*`

### Requirements

- 页面包含概览、物模型、设备实例、设备详情、操作与确认、设备事件、Provider 和消息通道。
- 首次进入使用 Admin Token 建立短期 Web Session；Token 不写入 localStorage，后续写请求携带 CSRF Token。
- 设备详情实时展示属性、observed_at、可写属性控件、Service 表单、风险标签、绑定和操作记录。
- 人工点击属性或 Service 直接调用交互 HTTP 路由，不弹出高危二次确认。
- 自动高危待确认操作在确认页支持 approve/reject；必须展示来源、动作摘要、过期时间和风险。
- 使用真实 API client；Mock 数据只能用于测试和 API 不可用时的明确演示状态。
- 视觉方向为“家庭设备控制台”：深石墨背景、低饱和电气蓝、设备在线绿、告警琥珀；4px spacing grid、边框型深度、紧凑工作台密度。
- 签名元素为“设备信号轨迹”：状态点、时间戳和 Provider 来源形成连续轨迹，用于概览、设备详情和操作记录。
- 使用语义化 HTML、可见 focus、44px 关键点击区、loading/empty/error/disabled 状态和 reduced-motion。
- 不使用渐变、装饰性色块、通用四宫格 SaaS 模板或无意义大数字。
- 前端 Token 与复用组件集中定义，不复制长串样式。

### Tests

Run:

```bash
cd modules/iot-mcp/web
npm install
npm run typecheck
npm run test -- --run
npm run build
```

### Commit

```text
feat(iot-mcp): add device management interface
```

## Task 6: End-to-end fixtures, operations docs, and full verification

### Goal

补齐跨模块 E2E、示例配置、运行说明和可复现验证，确保独立子模块可启动、可展示、可控制 Mock 设备。

### Files

- Create: `modules/iot-mcp/README.md`
- Create: `modules/iot-mcp/deploy/config.example.yaml`
- Create: `modules/iot-mcp/backend/tests/e2e/test_mock_device_flow.py`
- Create: `modules/iot-mcp/backend/tests/e2e/test_autonomous_high_risk_flow.py`
- Create: `modules/iot-mcp/backend/tests/e2e/test_failure_degradation.py`
- Modify only if needed for integration: files under `modules/iot-mcp/backend/` and `modules/iot-mcp/web/`

### Requirements

- README 提供依赖安装、Mock 启动、真实 HA 配置、Web/MCP 地址、消息签名回调示例和验证命令。
- 示例配置不包含真实 Secret，默认监听 `127.0.0.1`，Mock Provider 可开箱运行。
- E2E 覆盖人工直接控制高危门锁、MCP 自动高危待确认、签名批准后执行、低风险自动控制、幂等、HA/Provider 离线、超时和审计不可写。
- 构建后的 Web 必须能由 Backend 托管，直接打开设备页面并完成 Mock 控制。
- 不修改主项目 README 和已有架构文档；独立模块说明以本目录 README 为入口。
- 修复全量验证发现的模块内问题，不扩大到主项目其他区域。

### Tests

Run:

```bash
cd modules/iot-mcp/backend
uv run pytest -q
uv run ruff check src tests

cd ../web
npm run typecheck
npm run test -- --run
npm run build
```

### Commit

```text
test(iot-mcp): add e2e coverage and runbook
```
