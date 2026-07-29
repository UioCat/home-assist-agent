# IoT MCP

`modules/iot-mcp` 是可独立启动的 IoT 控制模块。它使用 SQLite 保存物模型、设备投影、操作和确认审计，以 Provider 实时读数作为设备状态事实源，并同时提供 Web、HTTP API 和 MCP 工具。

默认启用内存 Mock Provider、监听 `127.0.0.1`，Home Assistant（HA）默认关闭。无需真实设备即可展示和控制灯、空调与高风险门锁。

## 本地运行

前置依赖：

- Python 3.11+、[uv](https://docs.astral.sh/uv/)
- Node.js 20+、npm

先安装依赖并构建 Web。Backend 在进程启动时检测 `web/dist`，因此后构建时需要重启 Backend：

```bash
cd modules/iot-mcp/backend
uv sync --extra dev

cd ../web
npm ci
npm run build
```

设置本地占位凭据并启动 HTTP/Web 进程：

```bash
cd ../backend
export IOT_MCP_ADMIN_TOKEN='replace-local-admin-token'
export IOT_MCP_MACHINE_TOKENS='{"replace-local-machine-token":"local-agent"}'
export IOT_MCP_SESSION_SIGNING_SECRET='replace-with-random-session-secret'
export IOT_MCP_WEBHOOK_SECRET='replace-with-random-webhook-secret'
export IOT_MCP_DATABASE_URL='sqlite+aiosqlite:///./iot_mcp.db'
export IOT_MCP_SECURE_COOKIES='false'
uv run python -m iot_mcp --mode http
```

本地入口：

| Surface | 地址 / 命令 |
| --- | --- |
| Web 控制台 | `http://127.0.0.1:8090/` |
| HTTP API / OpenAPI | `http://127.0.0.1:8090/api/v1` / `http://127.0.0.1:8090/docs` |
| MCP CLI（stdio） | `uv run python -m iot_mcp --mode mcp --mcp-transport stdio` |
| MCP Streamable HTTP | `uv run python -m iot_mcp --mode mcp --mcp-transport streamable-http`，默认 `http://127.0.0.1:8091/mcp` |

Web 首次打开时用 Admin Token 换取短期 HttpOnly Session；后续写请求由 Session 绑定的 CSRF token 保护。`IOT_MCP_SECURE_COOKIES=false` 仅适用于上述本机 HTTP 演示。生产环境保留默认值 `true`，通过 TLS 访问，并从 Secret Manager 注入 Admin Token、Machine Token、Session 签名密钥和 Webhook 密钥。

MCP 和 HTTP 是独立进程入口；需要同时提供两者时启动两个进程，并让它们指向同一个持久化 SQLite。SQLite 适合单机部署，不用于多主写入。

## 配置

运行时通过 `IOT_MCP_` 环境变量读取配置，当前不读取 YAML，也没有 `--config` 参数。[deploy/config.example.yaml](deploy/config.example.yaml) 是部署值参考，其中每一项都标出实际环境变量，不能直接传给进程。

常用变量：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IOT_MCP_SERVER_HOST` / `IOT_MCP_SERVER_PORT` | `127.0.0.1` / `8090` | HTTP 与 Web |
| `IOT_MCP_MCP_HOST` / `IOT_MCP_MCP_PORT` | `127.0.0.1` / `8091` | MCP Streamable HTTP |
| `IOT_MCP_DATABASE_URL` | `sqlite+aiosqlite:///./iot_mcp.db` | SQLite async URL |
| `IOT_MCP_WEB_DIST_PATH` | 模块内 `web/dist` | 构建产物目录 |
| `IOT_MCP_MOCK_PROVIDER_ENABLED` | `true` | 开箱可用的 Mock Provider |
| `IOT_MCP_ADMIN_TOKEN` | 空 | 创建 Web Session；必须显式设置 |
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
| REST | `POST /api/services/{domain}/{service}` | 真实设备控制 |
| WebSocket | `/api/websocket` 的 `auth_required -> auth -> auth_ok` | WS 鉴权 |
| WebSocket | `config/entity_registry/list`，按 command `id` 关联响应 | Entity Registry 到 Device 的稳定归属 |
| WebSocket | `subscribe_events`，`event_type=state_changed` | Provider 事件订阅能力 |

真实控制只使用 HA Service API。`POST /api/states/{entity_id}` 只改变 HA 状态表示，不控制实体，本模块禁止用它控制设备。协议与注册表语义以 HA 官方文档为准：

- [REST API](https://developers.home-assistant.io/docs/api/rest/)
- [WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [Device Registry](https://developers.home-assistant.io/docs/device_registry_index/)
- [Entity Registry](https://developers.home-assistant.io/docs/entity_registry_index/)

当前独立 Runtime 在启动时执行全量同步，HTTP Provider 页面支持手动同步。HA Provider 已实现 `state_changed` 订阅契约，但当前 Runtime 尚未启动长期订阅或定时对账任务；页面实时查询仍直接读取 Provider。

## 控制与确认

| 调用来源 | 固定模式 | 高风险动作 |
| --- | --- | --- |
| 已认证 Web Session + CSRF | `human_interactive` | 通过校验后直接执行，不创建 Confirmation |
| MCP、Machine Token、Admin Token | `autonomous` | 只创建 `pending_confirmation`，Provider 不执行 |
| 自动低风险动作 | `autonomous` | 直接执行 |

调用方不能在请求体中声明 `interaction_mode` 或提升来源。所有写入要求 `Idempotency-Key`，先持久化 `ControlOperation`；相同 key 返回原操作，不重复产生 Provider 副作用。审计存储不可写时返回 `audit_unavailable` 并阻断设备写。Provider 超时记录为 `unknown`，不会声称设备已完成。

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
uv run pytest -q
uv run ruff check src tests

cd ../web
npm run typecheck
npm run test -- --run
npm run build
```

E2E 会通过真实应用容器、临时 SQLite、Mock Provider、HTTP/MCP tool surface 和构建后的 `web/dist` 验证控制链。若从干净工作树单独运行 Backend E2E，请先执行 `cd ../web && npm ci && npm run build`。

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
