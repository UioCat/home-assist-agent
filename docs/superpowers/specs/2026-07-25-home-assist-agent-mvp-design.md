# Home Assist Agent MVP 设计规格

日期：2026-07-25

## 1. 目标

构建一个本地运行、无需 Web 鉴权的最小家庭 IoT 助理：

- React 页面接收自然语言指令。
- Python 后端在入口处把指令分类为直接 IoT、间接 IoT或其他指令。
- 直接 IoT 指令由 Python 直接调用 Home Assistant MCP。
- 间接 IoT 指令由本地 Codex 生成受限工具计划，再由 Python 调用 Home Assistant MCP。
- 其他指令由本地 Codex 直接生成回复。
- 上层只暴露低、中、高三个 Codex 思考等级。

这个 MVP 不实现用户体系、持久化、多轮会话、任务调度、通知、完整审计和复杂设备管理。

## 2. 已确认的技术选择

| 范围 | 选择 |
| --- | --- |
| 后端 | Python、FastAPI、Pydantic |
| 前端 | React、Vite |
| IoT 通道 | Home Assistant 官方 MCP Server |
| MCP 客户端 | 官方 Python MCP SDK，Streamable HTTP |
| Codex 通道 | 本机 `codex exec`，非交互、临时会话 |
| Web 鉴权 | 不实现，仅监听本地地址 |
| 测试 | Pytest；前端使用 Vitest 和 React Testing Library |

Home Assistant MCP 默认地址为
`http://homeassistant.local:8123/api/mcp`，可以通过环境变量覆盖。
连接使用 Home Assistant Long-Lived Access Token。Token 只存在于后端环境变量，
不会发给浏览器。

## 3. 备选方案与决策

### 方案 A：Python 直接作为 HA MCP Client

这是采用的方案。

- Python 可以动态读取 HA MCP 工具和输入 schema。
- Python 拥有最终工具执行权，可以实施工具 allowlist 和参数检查。
- Codex 只生成回复或受限工具计划，不能直接绕过 Python 调用 IoT。
- 直接指令不需要 Codex，延迟低且失败边界清楚。

### 方案 B：让本地 Codex 自己连接 HA MCP

不采用。它虽然减少 MCP 客户端代码，但应用层难以验证 Codex 实际选择了什么工具，
也无法稳定区分计划失败、工具失败和最终回复失败。当前本机 Codex 配置中也没有发现
Home Assistant MCP。

### 方案 C：所有指令都交给 Codex 分类和执行

不采用。它不满足入口先分类再分发的要求，同时让显式开关灯也承担不必要的模型延迟。

## 4. 总体架构

```text
React Web
  -> POST /api/commands
  -> CommandService
       -> DirectCommandParser
            -> direct_iot
            -> SafetyPolicy
            -> HomeAssistantMcpClient
       -> CodexGateway
            -> indirect_iot + one ToolPlan
                 -> SafetyPolicy
                 -> HomeAssistantMcpClient
            -> other + answer
  <- CommandResponse + execution trace
```

后端另外提供：

```text
GET /api/health
  -> Backend status
  -> Local Codex availability/login status
  -> HA MCP configuration/connectivity
```

## 5. 指令分类契约

分类值固定为：

```text
direct_iot
indirect_iot
other
```

### 5.1 直接 IoT

直接指令必须包含明确动作和目标，并能由本地解析器无歧义地编译成一个工具调用。

第一版支持：

- `打开客厅灯`
- `关闭卧室灯`
- `把书房灯亮度设置为 30%`
- `客厅灯调到 60%`

对应工具：

- `HassTurnOn`
- `HassTurnOff`
- `HassLightSet`

工具名称按后缀匹配，以兼容 MCP 暴露的命名空间前缀。

### 5.2 间接 IoT

间接指令表达相对变化、环境感受或目标状态，必须结合推理或家庭实时状态才能决定动作。

示例：

- `客厅太暗了`
- `把客厅灯调暗一点`
- `我要看电影了`
- `屋里有点冷`

本地 Codex 接收用户指令和经过 allowlist 过滤后的实时 HA 工具 schema，返回结构化结果：

```json
{
  "category": "indirect_iot",
  "message": "准备将客厅灯调暗。",
  "tool_plan": {
    "tool_name": "HassLightSet",
    "arguments": {
      "area": "客厅",
      "brightness": 30
    }
  }
}
```

MVP 每次提交最多执行一个 HA MCP 工具。需要多个动作时返回解释，提示用户拆分指令。

### 5.3 其他指令

与家庭设备控制无关，或不应调用 IoT 工具的指令归为 `other`。

示例：

- `介绍一下你能做什么`
- `解释什么是 Home Assistant`
- `帮我想一个家庭自动化方案`

本地 Codex 在同一个结构化结果中返回文本回复，不携带 `tool_plan`。

### 5.4 分类顺序

1. 校验输入长度和空白。
2. `DirectCommandParser` 尝试生成直接动作。
3. 解析成功则分类为 `direct_iot`，不调用 Codex。
4. 解析失败则调用本地 Codex，一次完成 `indirect_iot` 与 `other` 的分类。
5. Codex 输出必须通过 Pydantic 模型校验。
6. Codex 输出不合法时不调用 HA，返回可观察错误。

## 6. Codex 封装

### 6.1 上层接口

```python
await codex_gateway.route(
    command: str,
    reasoning: Literal["low", "medium", "high"],
    tools: list[SafeToolDefinition],
) -> CodexRouteResult
```

上层不能传任意 Codex 配置。

思考等级映射：

| 产品等级 | Codex 配置 |
| --- | --- |
| 低 | `model_reasoning_effort=low` |
| 中 | `model_reasoning_effort=medium` |
| 高 | `model_reasoning_effort=high` |

### 6.2 本地命令

使用 `asyncio.create_subprocess_exec`，禁止 `shell=True`。用户指令通过标准输入传递，
不拼接到 Shell 字符串。

Codex 运行约束：

- `codex --ask-for-approval never exec`
- `--ephemeral`
- `--sandbox read-only`
- `--output-schema <schema-path>`
- `--output-last-message <temporary-output-path>`
- `--config model_reasoning_effort=<mapped-level>`

Codex 使用本机现有登录状态。MVP 不创建或存储 OpenAI API Key。

### 6.3 超时

默认超时：

| 等级 | 超时 |
| --- | --- |
| 低 | 45 秒 |
| 中 | 90 秒 |
| 高 | 150 秒 |

超时后终止子进程，不执行 HA MCP 工具，并返回 `codex_timeout`。

## 7. Home Assistant MCP 客户端

### 7.1 配置

```text
HA_MCP_URL=http://homeassistant.local:8123/api/mcp
HA_TOKEN=<long-lived-access-token>
HA_MCP_TIMEOUT_SECONDS=20
```

`HA_TOKEN` 必填。缺少配置时服务仍可启动，健康检查显示 `not_configured`；
其他类型的 Codex 对话仍可工作，IoT 请求返回明确错误。

### 7.2 会话行为

MVP 每次 IoT 请求创建一个 Streamable HTTP MCP 会话：

1. 使用 Bearer Token 建立 HTTP 客户端。
2. 初始化 MCP 会话。
3. 调用 `list_tools` 获取实时工具 schema。
4. 从工具列表中查找允许的工具。
5. 调用一次工具。
6. 关闭会话。

连接池和长连接生命周期优化不在 MVP 范围内。

### 7.3 安全 allowlist

允许暴露给 Codex或直接执行的工具后缀：

```text
HassTurnOn
HassTurnOff
HassLightSet
GetLiveContext
```

MVP 阻断以下目标关键词或参数：

```text
lock, door, garage, gas, water, camera,
门锁, 门禁, 车库, 燃气, 水阀, 摄像头
```

自定义脚本、自动化创建、广播、媒体播放、温控和其他未列出的工具不执行。
Home Assistant 自身的 exposed entities 配置仍是第二层设备可见范围。

## 8. HTTP API

### 8.1 提交指令

`POST /api/commands`

请求：

```json
{
  "command": "打开客厅灯",
  "reasoning": "medium"
}
```

响应：

```json
{
  "request_id": "01...",
  "category": "direct_iot",
  "route": "home_assistant_mcp",
  "status": "success",
  "message": "Home Assistant 已处理该指令。",
  "tool_call": {
    "name": "HassTurnOn",
    "arguments": {
      "name": "客厅灯"
    },
    "result": "Done"
  },
  "trace": [
    {
      "stage": "input",
      "status": "success",
      "summary": "收到指令"
    },
    {
      "stage": "classify",
      "status": "success",
      "summary": "直接 IoT"
    },
    {
      "stage": "dispatch",
      "status": "success",
      "summary": "Home Assistant MCP"
    }
  ],
  "elapsed_ms": 182
}
```

`status` 固定为：

```text
success
blocked
error
```

### 8.2 健康检查

`GET /api/health`

响应包含：

- `backend`
- `codex.installed`
- `codex.authenticated`
- `ha_mcp.configured`
- `ha_mcp.connected`
- `ha_mcp.tool_count`

健康检查不得返回 Token、完整环境变量或 Codex 凭据。

## 9. 错误处理

| 情况 | 行为 |
| --- | --- |
| 空指令 | HTTP 422 |
| 指令超过 1000 字符 | HTTP 422 |
| Codex 不存在 | `error / codex_not_found` |
| Codex 未登录 | `error / codex_not_authenticated` |
| Codex 超时 | `error / codex_timeout` |
| Codex 输出不合法 | `error / invalid_codex_output` |
| HA 未配置 | `error / ha_not_configured` |
| HA 401 | `error / ha_unauthorized` |
| HA 404 | `error / ha_mcp_not_enabled` |
| HA 超时或断连 | `error / ha_unavailable` |
| 高风险目标 | `blocked / unsafe_target` |
| Codex 选择非 allowlist 工具 | `blocked / tool_not_allowed` |
| 工具参数不匹配 schema | 不调用工具，返回 `invalid_tool_arguments` |

后端只有收到 MCP 工具成功结果后才返回 IoT `success`。模型生成的文字不能作为执行成功证据。

## 10. 前端产品设计

### 10.1 用户和任务

用户是在家中局域网内操作设备的 owner。打开页面后，他最重要的任务是输入一句指令并立即看懂：

- 系统把它判断成什么类型。
- 指令被交给了谁。
- 是否真的调用了 Home Assistant。
- 失败或阻断的具体原因。

### 10.2 视觉方向

领域词汇：

```text
家庭状态、设备控制、房间、指令、路由、执行、回执
```

自然颜色：

```text
石墨控制面板、陶瓷暖白、不锈钢灰、设备在线绿、告警琥珀、故障红
```

独特元素是“指令执行轨道”：每次提交都显示输入、分类、分发和结果四个节点。

拒绝三个常见默认：

- 不做通用聊天气泡列表，改为单次指令工作台。
- 不做大量相同设备卡片，首屏只突出指令入口。
- 不使用多种装饰色，只用颜色表达在线、推理、成功、阻断和错误。

### 10.3 页面结构

```text
Top bar
  Product name
  Codex status
  HA MCP status

Command workbench
  Large command textarea
  Low / Medium / High segmented radio
  Submit button
  Example command buttons

Execution result
  Category badge
  Input -> Classify -> Dispatch -> Result rail
  Final response
  Optional tool name and sanitized arguments
  Elapsed time
```

页面需要覆盖加载、空状态、成功、阻断和错误状态；支持桌面和手机宽度；交互控件使用原生
`button`、`textarea` 和 `input[type=radio]` 语义。

## 11. 工程结构

```text
pyproject.toml
.env.example
src/home_assist_agent/
  api/
    app.py
    models.py
  commands/
    classifier.py
    models.py
    service.py
  codex/
    gateway.py
    schemas/
      route_result.json
  ha/
    mcp_client.py
    safety.py
  settings.py
  __main__.py
tests/
  test_direct_command_parser.py
  test_safety_policy.py
  test_command_service.py
  test_codex_gateway.py
  test_api.py
frontend/
  package.json
  vite.config.js
  src/
    App.jsx
    api.js
    components/
      CommandWorkbench.jsx
      ExecutionRail.jsx
      HealthStatus.jsx
    styles.css
  tests/
    App.test.jsx
scripts/
  dev.py
```

前端生产构建产物由 FastAPI 静态托管；开发模式下 Vite 代理 `/api` 到 FastAPI。

## 12. 测试策略

实现采用测试驱动开发。

### 12.1 后端单元测试

- 明确开、关、绝对亮度指令被编译为直接动作。
- 相对变化、环境感受和普通问题不被直接解析器误执行。
- 高风险目标被阻断。
- 非 allowlist 工具被阻断。
- 低、中、高正确映射到 Codex CLI 配置。
- 用户输入只通过 stdin 传给 Codex，不进入 shell 命令。
- Codex 超时会终止进程。
- Codex 结构化输出必须通过模型校验。

### 12.2 服务编排测试

使用内存 fake Codex 和 fake MCP client：

- 直接指令不调用 Codex，只调用一次 MCP。
- 间接指令调用一次 Codex，再调用一次 MCP。
- 其他指令调用一次 Codex，不调用 MCP。
- Codex 返回危险或未知工具时不调用 MCP。
- MCP 失败不会返回执行成功。

### 12.3 API 和前端测试

- API 请求校验和三类响应契约。
- 页面可以选择思考等级并提交指令。
- 加载时按钮禁用。
- 成功、阻断和错误结果正确呈现。
- 健康状态不会泄露 Token。

### 12.4 验证

- `pytest`
- `npm test`
- `npm run build`
- 启动本地服务后调用 `/api/health`
- 在桌面和手机视口执行浏览器冒烟测试
- 有 HA 配置时至少验证一次真实 `GetLiveContext`
- 只有用户明确提供可安全测试的灯光目标时，才执行真实写操作冒烟测试

## 13. MVP 完成标准

- 一个本地 React 页面可以提交指令。
- 后端明确返回 `direct_iot`、`indirect_iot` 或 `other`。
- 直接指令无需 Codex即可经过安全检查调用 HA MCP。
- 间接指令通过所选思考等级调用本地 Codex，并最多执行一个安全 HA MCP 工具。
- 其他指令可以通过本地 Codex 返回文本。
- 页面可以看到分类、分发、结果和错误原因。
- 后端测试、前端测试和前端构建全部通过。
- 未配置 HA 时，应用仍可启动并进行非 IoT Codex 交互。
