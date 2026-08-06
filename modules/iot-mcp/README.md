# IoT MCP

`modules/iot-mcp` 是可独立启动的 IoT 控制模块。它使用 SQLite 保存物模型、设备投影、操作和确认审计，以 Provider 实时读数作为设备状态事实源，并同时提供统一 Web 控制台、HTTP API 和 MCP 工具。统一控制台还包含根项目的家庭指令与主流程审计页面，但两个后端进程仍互不依赖。

默认启用内存 Mock Provider、监听 `127.0.0.1`，Home Assistant（HA）默认关闭。无需真实设备即可展示和控制灯、空调与高风险门锁。

## 本地运行

前置依赖：

- Python 3.11+、[uv](https://docs.astral.sh/uv/)
- Node.js 20+、npm

从仓库根目录启动统一前端和两个后端：

```bash
.venv/bin/python scripts/dev_all.py
```

浏览器打开 `http://127.0.0.1:5173`。该命令分别使用 `5173`、`8080` 和 `8090`，并从根目录本地 `.env` 向两个后端注入各自需要的 HA 配置；凭据不会打印到终端。

若 `5173` 被其他项目占用，可运行 `.venv/bin/python scripts/dev_all.py --frontend-port 5174`；只改变统一前端端口。

以下步骤用于只运行 IoT 模块或生产构建。

先安装依赖并构建 Web。Backend 在进程启动时检测 `web/dist`，因此后构建时需要重启 Backend：

```bash
cd modules/iot-mcp/backend
uv sync --extra dev

cd ../web
npm ci
npm run build
```

启动 HTTP/Web 进程。当前本地开发默认关闭鉴权，不需要 Admin Token：

```bash
cd ../backend
export IOT_MCP_DATABASE_URL='sqlite+aiosqlite:///./iot_mcp.db'
uv run python -m iot_mcp --mode http
```

本地入口：

| Surface | 地址 / 命令 |
| --- | --- |
| 统一 Web 控制台（生产构建） | `http://127.0.0.1:8090/` |
| HTTP API / OpenAPI | `http://127.0.0.1:8090/api/v1` / `http://127.0.0.1:8090/docs` |
| MCP CLI（stdio） | `uv run python -m iot_mcp --mode mcp --mcp-transport stdio` |
| MCP Streamable HTTP | `uv run python -m iot_mcp --mode mcp --mcp-transport streamable-http`，默认 `http://127.0.0.1:8091/mcp` |

免鉴权模式把浏览器请求作为本地交互式 `owner` 处理，但不会绕过风险策略、确认流程或控制审计。该模式只适用于默认绑定 `127.0.0.1` 的本地开发。

需要恢复鉴权时设置 `IOT_MCP_AUTH_ENABLED=true`，并通过安全配置渠道注入 Admin Token、Machine Token、Session 签名密钥和 Webhook 密钥。Web 首次打开时会用 Admin Token 换取短期 HttpOnly Session，后续写请求由 Session 绑定的 CSRF token 保护。使用本机 HTTP 测试鉴权时还需设置 `IOT_MCP_SECURE_COOKIES=false`；生产环境应通过 TLS 保持该项为 `true`。

MCP 和 HTTP 是独立进程入口；需要同时提供两者时启动两个进程，并让它们指向同一个持久化 SQLite。SQLite 适合单机部署，不用于多主写入。

## 配置

运行时通过 `IOT_MCP_` 环境变量读取配置，当前不读取 YAML，也没有 `--config` 参数。[deploy/config.example.yaml](deploy/config.example.yaml) 是部署值参考，其中每一项都标出实际环境变量，不能直接传给进程。

常用变量：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IOT_MCP_SERVER_HOST` / `IOT_MCP_SERVER_PORT` | `127.0.0.1` / `8090` | HTTP 与 Web |
| `IOT_MCP_MCP_HOST` / `IOT_MCP_MCP_PORT` | `127.0.0.1` / `8091` | MCP Streamable HTTP |
| `IOT_MCP_DATABASE_URL` | `sqlite+aiosqlite:///./iot_mcp.db` | SQLite async URL |
| `IOT_MCP_AUDIT_DATABASE_PATH` | `./iot_mcp_audit.db` | 追加写审计库；不可写时阻断外部调用 |
| `IOT_MCP_WEB_DIST_PATH` | 模块内 `web/dist` | 构建产物目录 |
| `IOT_MCP_MOCK_PROVIDER_ENABLED` | `true` | 开箱可用的 Mock Provider |
| `IOT_MCP_RECONCILE_INTERVAL_SECONDS` | `600` | Provider 全量对账间隔 |
| `IOT_MCP_PROVIDER_RECONNECT_DELAY_SECONDS` | `1` | Provider 事件订阅断开后的重连等待 |
| `IOT_MCP_AUTH_ENABLED` | `false` | 是否启用 Admin Token、Web Session 与 CSRF 鉴权 |
| `IOT_MCP_ADMIN_TOKEN` | 空 | 开启鉴权后用于创建 Web Session |
| `IOT_MCP_MACHINE_TOKENS` | `{}` | JSON 对象：`token -> actor id` |
| `IOT_MCP_SESSION_SIGNING_SECRET` | 进程随机值 | 生产必须固定注入，否则重启后 Session 失效 |
| `IOT_MCP_WEBHOOK_SECRET` | 进程随机值 | 生产必须固定注入，供消息 HMAC |
| `IOT_MCP_ALLOWED_CONFIRMATION_ACTORS` | `["owner"]` | JSON 数组 |
| `IOT_MCP_SECURE_COOKIES` | `true` | 生产安全默认值 |

## Home Assistant

设置 URL 与 Long-Lived Access Token 后启用 HA；两者缺一时 HA Provider 不创建：

```bash
export IOT_MCP_HOME_ASSISTANT_URL='http://homeassistant.local:8123'
export IOT_MCP_HOME_ASSISTANT_TOKEN='replace-with-ha-long-lived-access-token'
export IOT_MCP_HOME_ASSISTANT_TIMEOUT_SECONDS='10'
# 可选：只启用 HA
export IOT_MCP_MOCK_PROVIDER_ENABLED='false'
uv run python -m iot_mcp --mode http
```

当前 HA Adapter 使用的原生接口如下：

| 协议 | 接口 / 命令 | 当前用途 |
| --- | --- | --- |
| REST | `GET /api/states` | 初始实体与状态同步 |
| REST | `GET /api/states/{entity_id}` | 控制前后与实时状态读取 |
| REST | `GET /api/config`、`GET /api/services` | HA 实例元数据与可调用 Service 发现 |
| REST | `POST /api/services/{domain}/{service}` | 真实设备控制 |
| WebSocket | `/api/websocket` 的 `auth_required -> auth -> auth_ok` | WS 鉴权 |
| WebSocket | `config/entity_registry/list`、`config/device_registry/list`、`config/area_registry/list` | Entity、Device 与 Area 的稳定归属和展示元数据 |
| WebSocket | `subscribe_events`，`event_type=state_changed` | 实时状态事件 |

真实控制只使用 HA Service API。`POST /api/states/{entity_id}` 只改变 HA 状态表示，不控制实体，本模块禁止用它控制设备。协议与注册表语义以 HA 官方文档为准：

- [REST API](https://developers.home-assistant.io/docs/api/rest/)
- [WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [Device Registry](https://developers.home-assistant.io/docs/device_registry_index/)
- [Entity Registry](https://developers.home-assistant.io/docs/entity_registry_index/)

Runtime 启动时执行一次全量同步，随后持有每个 Provider 的长期事件订阅，并按配置间隔执行全量对账。订阅结束或异常时 Provider 标记为 degraded 并自动重连；事件会写入属性快照和设备事件，进程关闭时会关闭订阅、取消后台任务并释放 Provider/数据库资源。HTTP Provider 页面仍支持手动触发同步，页面实时查询直接读取 Provider。

每次启动同步、HA REST/WebSocket 调用、HTTP/MCP 请求和 Provider 事件都使用同一个 `message_id`（兼容字段 `request_id` 与其相同）写入追加式审计链。请求体、响应体和失败信息会记录，Token、Cookie、密码及其他凭据会在持久化前统一脱敏。

## 物模型生命周期

Provider 同步生成的系统产品使用稳定、产品级能力标识；能力变化会生成新版本、归档旧 active 版本，并把设备和 Feature Binding 一并切换到新版本。每台设备持久化精确的 `model_version_id`，控制校验与 Web 控件都只使用该绑定版本，未知能力标识会拒绝执行。

人工导入只接受标准 TSL JSON，并创建不可变 `draft`。草稿可校验、导出、发布或归档；同一产品最多一个 active 版本，只有显式发布后才会替换当前 active 并更新已有设备绑定。系统生成产品不允许通过人工导入覆盖身份。

## 控制与确认

| 调用来源 | 固定模式 | 高风险动作 |
| --- | --- | --- |
| 已认证 Web Session + CSRF | `human_interactive` | 通过校验后直接执行，不创建 Confirmation |
| MCP、Machine Token、Admin Token | `autonomous` | 只创建 `pending_confirmation`，Provider 不执行 |
| 自动低风险动作 | `autonomous` | 直接执行 |

调用方不能在请求体中声明 `interaction_mode` 或提升来源。所有 HTTP 写入要求 `Idempotency-Key`，MCP 写工具要求调用方提供稳定的 `idempotency_key` 并在 MCP 命名空间内使用。请求先持久化 `ControlOperation`；同一 key 与相同语义指纹返回原操作，不重复产生 Provider 副作用，key 被不同设备、动作、来源或绑定复用时返回 `idempotency_conflict`。审计存储不可写时返回 `audit_unavailable` 并阻断设备写。Provider 控制请求超时记录为 `unknown`，不会声称设备已完成或提示安全重试。

MCP 暴露查询物模型、设备、实时状态、操作和事件，以及 `set_device_properties`、`invoke_device_service`。MCP 不提供批准或拒绝工具；自动高风险确认只能通过已认证 Web 路由或签名消息回调。

## Signed Webhook 回调

回调地址为：

```text
POST /api/v1/message-channels/signed-webhook/callbacks
```

请求体必须使用签名时的原始 JSON bytes，字段为 `actor`、`decision`（`approve` 或 `reject`）、`confirmation_id` 和 `action_hash`。签名 canonical input 精确为：

```text
ASCII(timestamp) + "." + nonce + "." + raw_body_bytes
```

签名算法为 HMAC-SHA256，十六进制小写摘要放入 `X-IoT-Signature: sha256=<hex>`。另外发送 `X-IoT-Timestamp`（Unix 秒）和一次性 `X-IoT-Nonce`。时间戳默认容差 300 秒；nonce 在 SQLite 中原子消费，重复回调返回 `webhook_replay`。

以下示例不包含真实密钥，body 的空格与字段顺序必须保持和计算签名时完全一致：

```bash
export IOT_MCP_WEBHOOK_SECRET='replace-with-the-same-webhook-secret'
BODY='{"actor":"owner","decision":"approve","confirmation_id":"replace-confirmation-id","action_hash":"replace-action-hash"}'
TIMESTAMP="$(date +%s)"
NONCE="example-$(date +%s)-001"
SIGNATURE="$(
  BODY="$BODY" TIMESTAMP="$TIMESTAMP" NONCE="$NONCE" python3 - <<'PY'
import hashlib
import hmac
import os

raw = os.environ["BODY"].encode()
canonical = (
    f'{os.environ["TIMESTAMP"]}.{os.environ["NONCE"]}.'.encode() + raw
)
print(
    hmac.new(
        os.environ["IOT_MCP_WEBHOOK_SECRET"].encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()
)
PY
)"
curl --fail-with-body \
  -H 'Content-Type: application/json' \
  -H "X-IoT-Timestamp: $TIMESTAMP" \
  -H "X-IoT-Nonce: $NONCE" \
  -H "X-IoT-Signature: sha256=$SIGNATURE" \
  --data-binary "$BODY" \
  http://127.0.0.1:8090/api/v1/message-channels/signed-webhook/callbacks
```

批准只会执行 Confirmation 中已绑定的设备、动作参数、Provider binding revision 和 action hash；actor、hash、有效期或绑定不匹配时拒绝执行。

## 验证

完整验证：

```bash
cd modules/iot-mcp/backend
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests

cd ../web
npm ci
npm run typecheck
npm run test -- --run
npm run build
```

Backend E2E 通过真实应用容器、临时 SQLite、Mock Provider 和 HTTP/MCP surface 验证控制链；其中确定性的静态 Web fixture 只覆盖 SPA fallback，不代表浏览器覆盖。真实 React-to-FastAPI 验收使用 Playwright CLI：它从干净依赖安装开始构建 `web/dist`，启动 FastAPI，完成登录、低风险控制、人工高风险直控、自动高风险确认和 reload/Session 恢复。

```bash
export PWCLI="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
./scripts/browser_e2e.sh
```

脚本使用隔离浏览器 Session 与临时 SQLite，完成后清理运行时文件。需要 Node/npm、uv、curl，以及可用的 `playwright-cli` 或上述 wrapper。

无常驻进程的 CLI 解析冒烟：

```bash
cd modules/iot-mcp/backend
uv run python -m iot_mcp --help
uv run python -m iot_mcp --mode mcp --mcp-transport stdio --help
```

## 模块边界

- 本模块独立启动、独立存储，不修改或穿透主项目数据库与调用链。
- TSL 描述能力和 Provider 绑定；本地快照仅用于展示与诊断，Provider 实时状态才是执行事实源。
- 非 HA 设备当前只有通用 `DeviceProvider` 契约和内置 Mock，没有 `LanHttpDeviceProvider` 的真实厂商发现、认证、协议或网络控制实现。
- 不解析自然语言、不编排自动化规则，也不使用 HA 官方 MCP 作为底层设备 SDK。
