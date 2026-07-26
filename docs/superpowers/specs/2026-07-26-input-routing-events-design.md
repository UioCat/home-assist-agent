# Home Assist Agent 输入路由与事件处理设计

日期：2026-07-26
状态：已确认

## 1. 背景与目标

当前 `POST /api/commands` 在本地直接指令解析未命中后，会先查询 Home
Assistant MCP 工具，再由 Codex 判断请求属于模糊设备控制还是普通请求。
因此普通请求也会产生不必要的 HA `list_tools` 调用，消息入口、意图分类、
Codex 调用和设备执行的职责也集中在同一个服务中。

当前方案将输入处理拆成独立模块，并增加最小可用的事件入口：

- 用户消息进入统一消息通道后，先由低思考等级 Codex 完成意图分类。
- 普通请求不读取 HA 工具，不受 HA 状态影响。
- 直接控制由路由结果携带抽象设备指令。
- 模糊控制在分类后读取 HA 实时能力，再由中思考等级 Codex 生成工具计划。
- 普通请求由高思考等级 Codex 直接回答。
- 外部事件先去重、记录和更新上下文，只有命中显式自动化规则时才产生设备意图。
- 消息、事件、Codex、HA 和错误分支继续使用完整、可查询的全链路审计。

## 2. 当前方案边界

### 2.1 包含范围

| 范围 | 当前方案 |
| --- | --- |
| 消息入口 | 保留 `POST /api/commands`，由 Web 消息通道接入 |
| 指令路由 | Codex 固定使用 `low`，输出结构化分类 |
| 直接控制 | 路由输出抽象设备指令，读取实时 HA 工具后执行 |
| 模糊控制 | Codex 固定使用 `medium`，结合安全工具生成一个工具计划 |
| 普通请求 | Codex 固定使用 `high`，只接收通用 system prompt 和用户原始 prompt |
| 事件入口 | 新增 `POST /api/events`，支持幂等接收和上下文更新 |
| 自动化 | 提供规则引擎接口，默认没有启用的自动执行规则 |
| 审计 | 扩展关联字段和 Codex 调用目的，保持追加写入与凭据脱敏 |

### 2.2 不包含范围

- 不接入具体摄像头、人体传感器或消息平台 SDK。
- 不提供自动化规则的管理页面或配置语言。
- 不允许 Codex 自主决定未配置事件是否需要执行设备动作。
- 不扩大现有 HA 工具 allowlist 和高风险设备范围。
- 不实现跨家庭、多用户身份解析和长期行为推断。

## 3. 模块边界

```text
HTTP / Future Channel Adapter
  -> MessageChannel / EventChannel
  -> CommandOrchestrator / EventOrchestrator
       -> InstructionRouter
       -> CodexService
       -> HouseholdContextStore
       -> AutomationRuleEngine
       -> DeviceExecutor
            -> SafetyPolicy
            -> HomeAssistantMcpClient
  -> Channel Response
```

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `channels.message` | 接收用户消息、生成或透传 `message_id`、审计用户输入与最终输出 | 不分类意图，不访问 HA |
| `channels.event` | 校验和标准化事件、生成稳定链路 ID、执行幂等检查 | 不自主生成设备动作 |
| `routing` | 使用 Codex `low` 返回结构化路由决策 | 不读取 HA 工具，不执行设备 |
| `codex` | 封装路由、设备规划和普通回答三种 Codex 调用 | 不拥有 HA 执行权 |
| `orchestration` | 根据路由结果编排分支、错误和执行轨迹 | 不自行通过关键词猜测意图 |
| `context` | 保存人员位置、活动等最新家庭上下文 | 不执行自动化 |
| `automation` | 用显式规则把事件转换为派生设备意图 | 默认规则为空，不让 Codex 替代规则 |
| `devices` | 获取实时 HA 工具、映射指令、安全校验、Schema 校验和执行 | 不处理普通请求 |

`DirectCommandParser` 不再负责入口分类。设备动作到 MCP 工具的确定性映射可以保留，
但作为 `DeviceExecutor` 的内部编译步骤。

## 4. 核心数据契约

### 4.1 路由决策

```python
RouteDecision = {
    "category": "direct_iot|indirect_iot|other",
    "device_command": {
        "action": "turn_on|turn_off|set_brightness",
        "target": "string",
        "parameters": {}
    } | None,
    "intent_summary": "string|null"
}
```

一致性规则：

| 分类 | `device_command` | `intent_summary` |
| --- | --- | --- |
| `direct_iot` | 必填 | 可空 |
| `indirect_iot` | 必须为空 | 必填 |
| `other` | 必须为空 | 必须为空 |

直接设备指令第一期支持：

| 抽象动作 | MCP 工具后缀 | 参数 |
| --- | --- | --- |
| `turn_on` | `HassTurnOn` | `{"name": target}` |
| `turn_off` | `HassTurnOff` | `{"name": target}` |
| `set_brightness` | `HassLightSet` | `{"name": target, "brightness": 0..100}` |

路由结果不符合模型约束时返回 `invalid_route_output`，不得访问 HA。

### 4.2 入站事件

`POST /api/events` 请求：

```json
{
  "event_id": "ha-event-123",
  "event_type": "person.seated",
  "source": "home_assistant",
  "subject_id": "owner",
  "location": "study",
  "occurred_at": "2026-07-26T16:30:00+08:00",
  "attributes": {
    "confidence": 0.96
  },
  "correlation_id": "home-session-456",
  "causation_id": null
}
```

约束：

- `source + event_id` 是幂等键。
- 事件链路 `message_id` 固定为
  `event_ + sha256(source + "\0" + event_id).hexdigest()`；兼容字段
  `request_id` 与其相同。
- `correlation_id` 串联“进屋、进入书房、坐下”等同一场景事件。
- `causation_id` 指向产生当前事件或派生动作的上游事件。
- 重复事件返回相同 `message_id`，不重复更新上下文、调用 Codex 或执行 HA。

响应：

```json
{
  "message_id": "event_<stable-id>",
  "request_id": "event_<stable-id>",
  "status": "observed|duplicate|triggered",
  "event_type": "person.seated",
  "rule_id": null
}
```

### 4.3 家庭上下文

第一期上下文保存事件产生的最新事实：

```python
HouseholdContextEntry = {
    "subject_id": "string",
    "event_type": "string",
    "location": "string|null",
    "attributes": {},
    "source_message_id": "string",
    "occurred_at": "datetime",
    "updated_at": "datetime"
}
```

上下文表可以更新，但每次变化必须先追加审计事件。审计表本身仍禁止更新和删除。

### 4.4 派生设备意图

```python
DerivedDeviceIntent = {
    "rule_id": "string",
    "prompt": "string",
    "source_message_id": "string",
    "correlation_id": "string|null",
    "causation_id": "string"
}
```

规则命中后，派生意图进入相同的低思考等级指令路由流程，并沿用原事件
`message_id`。后续 Codex、HA 请求与响应都记录在同一审计链路中。

## 5. 用户消息数据流

### 5.1 公共入口

```text
MessageChannel
  -> audit user.request
  -> InstructionRouter.route(reasoning=low)
  -> CommandOrchestrator
  -> audit user.response
```

原请求中的 `reasoning` 字段保留为兼容字段，但不再影响执行路径。Web 页面移除思考等级
选择，实际等级由分支固定决定。

### 5.2 直接设备控制

```text
RouteDecision(direct_iot + device_command)
  -> HomeAssistantMcpClient.list_tools
  -> SafetyPolicy.resolve_tool
  -> DeviceCommandCompiler
  -> JSON Schema validation
  -> HomeAssistantMcpClient.call_tool
  -> CommandResponse
```

该路径只调用一次 Codex，思考等级固定为 `low`。工具请求审计写入失败时必须阻断
HA 调用。

### 5.3 模糊设备控制

```text
RouteDecision(indirect_iot + intent_summary)
  -> HomeAssistantMcpClient.list_tools
  -> SafetyPolicy.filter_tool_names
  -> CodexService.plan_device_control(reasoning=medium)
  -> ToolPlan validation
  -> DeviceExecutor
  -> CommandResponse
```

设备规划 prompt 必须包含：

- 明确说明该请求需要控制设备。
- 用户原始 prompt。
- 路由器输出的 `intent_summary`。
- 经过安全过滤的实时 HA 工具及其 input schema。

一次请求仍最多执行一个 MCP 工具。

### 5.4 普通请求

```text
RouteDecision(other)
  -> CodexService.answer(reasoning=high)
  -> CommandResponse
```

普通回答 prompt 只包含通用 system prompt 和用户原始 prompt，不包含：

- HA 工具、状态或连接错误。
- 路由分类结果。
- `intent_summary`。
- “需要控制设备”等设备规划说明。

因此该路径不得出现 HA `list_tools` 或 `call_tool` 审计事件。

## 6. 事件数据流

```text
EventChannel
  -> validate and normalize
  -> audit event.received
  -> idempotency check
       -> duplicate: audit event.duplicate -> return
  -> audit context.update.request
  -> HouseholdContextStore.upsert
  -> audit context.update.response
  -> AutomationRuleEngine.evaluate
       -> no match: audit automation.no_match -> return observed
       -> match: audit automation.matched
                 -> create DerivedDeviceIntent
                 -> InstructionRouter.route(low)
                 -> device branch
                 -> return triggered
```

默认 `AutomationRuleEngine` 没有启用规则，因此新接入的事件只会记录和更新上下文。
后续启用的规则必须具有稳定 `rule_id`，并明确允许的事件类型、条件和派生 prompt。

示例：

```text
person.entered_home
  -> 更新 owner.location=home
  -> 无规则
  -> observed

person.seated(location=study)
  -> 更新 owner.location=study、owner.activity=seated
  -> 无规则
  -> observed
```

## 7. Codex 调用契约

Codex 封装提供三个明确入口：

```python
await codex.route(command, message_id) -> RouteDecision
await codex.plan_device_control(
    command,
    intent_summary,
    tools,
    message_id,
) -> ToolPlan
await codex.answer(command, message_id) -> AnswerResult
```

| 入口 | `purpose` | 思考等级 | 是否含 HA 工具 |
| --- | --- | --- | --- |
| `route` | `route` | `low` | 否 |
| `plan_device_control` | `device_plan` | `medium` | 是 |
| `answer` | `answer` | `high` | 否 |

三个入口共用安全的子进程执行器，但使用独立 system prompt 和输出 schema。审计必须保存：

- `purpose`、完整 prompt、思考等级、命令参数和超时。
- 结构化输出、原始输出、标准输出、标准错误和失败信息。
- 同一个 `message_id`、`correlation_id` 和 `causation_id`。

## 8. 错误、安全与降级

| 场景 | 处理 |
| --- | --- |
| 路由 Codex 失败或输出非法 | 返回路由错误；不访问 HA |
| 普通回答 Codex 失败 | 返回回答错误；不访问 HA |
| 模糊规划 Codex 失败 | 返回规划错误；不执行 HA |
| HA 未配置或不可用 | 只影响设备分支；普通请求正常 |
| 直接指令字段不完整 | 返回 `invalid_route_output`；不访问 HA |
| 工具不在 allowlist | 返回 `blocked` |
| 参数不符合实时 schema | 返回 `invalid_tool_arguments` |
| 事件重复 | 返回 `duplicate`；不重复更新或执行 |
| 上下文写入失败 | 返回事件处理错误；不评估规则 |
| 自动化规则未命中 | 返回 `observed`；不调用 Codex 或 HA |
| 自动化命中高风险目标 | 由现有安全策略阻断 |
| 审计请求写入失败 | 阻断后续外部调用 |

事件 payload、Codex prompt 和外部服务数据在持久化前统一脱敏。Token、Authorization、
Cookie、密码和客户端密钥不得进入审计库或 API 响应。

## 9. 审计与关联字段

`AuditEvent` 增加可空字段：

```text
correlation_id
causation_id
```

SQLite 初始化时对已有数据库执行向前兼容的加列迁移，不修改历史事件内容。

核心事件类型：

| 模块 | 事件类型 |
| --- | --- |
| 消息通道 | `user.request`、`user.response` |
| 事件通道 | `event.received`、`event.duplicate`、`event.response` |
| 上下文 | `context.update.request`、`context.update.response` |
| 自动化 | `automation.no_match`、`automation.matched` |
| Codex | `codex.request`、`codex.response`，payload 包含 `purpose` |
| 外部服务 | `external.request`、`external.response` |

审计中心按 `message_id` 显示完整链路，并补充事件、上下文和自动化事件标签。
`list_tools` 显示为“工具目录查询”，`call_tool` 显示为“设备业务调用”，避免把能力发现误解为
设备控制。

## 10. API 与兼容性

- `POST /api/commands` 请求与响应保持兼容。
- `reasoning` 请求字段继续接受 `low|medium|high`，但标记为兼容字段并忽略。
- `message_id` 和 `request_id` 始终相同。
- 新增 `POST /api/events`。
- 现有 `/api/audit` 和 `/api/audit/{message_id}` 保持兼容，并返回新增关联字段。
- Web 指令中心移除思考等级选择，执行轨迹显示实际分支及固定思考等级。
- 审计中心增加 Codex `purpose`、事件输入、上下文和自动化标签。

## 11. 测试与验收

### 11.1 用户消息

| 场景 | Codex 调用 | HA 调用 |
| --- | --- | --- |
| 明确开关或亮度控制 | `route(low)` 一次 | `list_tools`、`call_tool` |
| 模糊设备控制 | `route(low)`、`device_plan(medium)` | 分类后才 `list_tools`，计划后 `call_tool` |
| 普通请求 | `route(low)`、`answer(high)` | 0 次 |

必须验证三条路径的 prompt 内容、结构化输出、执行轨迹、错误码和审计顺序。

### 11.2 事件

- 新事件写入事件审计并更新上下文。
- 重复 `source + event_id` 返回相同 `message_id`，不重复更新或执行。
- 默认无规则时不调用 Codex 和 HA。
- 规则命中后使用同一 `message_id` 进入路由，并设置正确的关联字段。
- 事件上下文写入失败时不评估规则。
- 高风险派生动作被安全策略阻断。

### 11.3 全链路约束

- 成功、失败、阻断和审计不可用路径都有测试。
- 凭据脱敏测试覆盖嵌套字段、Header 和字符串形式。
- 普通请求审计中不存在 `home_assistant_mcp` 事件。
- 未命中规则的事件审计中不存在 Codex 和 HA 事件。
- SQLite 历史审计仍不可更新或删除，并可迁移新增关联列。

## 12. 实施顺序

1. 拆分 Codex 路由、设备规划和普通回答契约及 schema。
2. 实现结构化 `InstructionRouter` 和固定思考等级。
3. 提取消息通道与命令编排模块，迁移用户输入/输出审计。
4. 提取设备执行模块，复用安全和实时 schema 校验。
5. 增加事件契约、事件通道、幂等存储和上下文存储。
6. 增加默认空规则引擎与派生意图接口。
7. 扩展审计关联字段、查询和审计中心标签。
8. 更新 Web 思考等级展示、README、API 测试和端到端链路测试。
