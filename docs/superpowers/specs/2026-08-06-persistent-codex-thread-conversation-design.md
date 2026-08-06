# 持久化 Codex Thread 会话设计

日期：2026-08-06
状态：待用户复核

## 1. 背景与目标

当前家庭指令中心会针对路由、目标解析、计划和普通回答分别执行一次 `codex exec --ephemeral`。这些调用互相独立，因此上一轮用户说过的房间、设备、偏好或省略指代不会自然进入下一轮，也无法让控制台、语音和微信形成连续对话。

本方案将当前家庭中的所有用户入口映射到同一个应用会话，并让该应用会话直接、持续地复用同一个 Codex thread。现阶段尚未实现用户识别，因此统一使用：

- `home_id = local-home`
- `person_id = local-user`

会话默认长期有效，不因闲置自动过期。只有用户明确执行“新建会话”时，才停止使用当前 thread 并创建新的会话。未来增加身份识别时，只需更换入口身份解析，不改变会话和审计模型。

## 2. 核心决策

1. 一个应用 `conversation_id` 与一个不透明的 `codex_thread_id` 一一绑定。
2. Codex thread 是普通对话上下文的权威来源；应用不在每次请求中重新拼装最近消息或摘要来模拟上下文。
3. 应用仍持久化会话映射、消息回执、幂等状态和完整审计。审计记录用于追踪与展示，不作为 Codex 上下文替代品。
4. 当前控制台、语音和微信等用户入口都解析到 `local-user` 的当前活动会话。
5. 不再使用 `--ephemeral`；后续调用必须通过已保存的精确 thread ID 执行 `codex exec resume <thread-id>`，禁止使用 `--last`。
6. 每个用户消息内的路由、目标解析、计划、结果提交等 Codex 调用都在同一个 thread 上串行完成。
7. thread 中的历史可以帮助理解“它”“再暗一点”“刚才那个”等语义，但不能作为设备当前状态的事实来源。执行前必须重新从 Home Assistant/IOT 后端查询设备目录、能力、可用性和实时状态。

## 3. 标识与不变量

- `message_id` 标识一次用户消息或系统事件；每条消息唯一。
- 兼容字段 `request_id` 必须始终与 `message_id` 相同。
- `conversation_id` 跨越多条消息，标识应用会话。
- `codex_thread_id` 仅由后端保存和使用，不允许客户端指定任意外部 thread。
- 同一消息产生的 Codex、MCP、HTTP、Home Assistant 调用及错误分支，必须同时携带同一个 `message_id`、`request_id` 和 `conversation_id`。
- 相同 `message_id` 的重试必须返回已保存的回执，不得重复调用 Codex 或重复执行设备操作。
- 每个外部副作用调用都必须先成功写入请求审计；审计不可用时直接阻断调用。
- 凭据在进入持久化 Codex prompt 或审计库前统一脱敏。Authorization、Token、API Key、Cookie、密码和客户端密钥不能出现在会话、回执或审计记录中。

## 4. 组件设计

### 4.1 ConversationCoordinator

新增统一的会话协调器，职责包括：

- 根据服务端解析出的 `home_id/person_id` 获取当前活动会话。
- 没有活动会话时创建一个 `creating` 状态的会话。
- 首次 Codex 调用成功后，原子绑定 `codex_thread_id` 并转为 `active`。
- 按 `conversation_id` 串行处理消息，保证 Codex turn 的顺序稳定。
- 处理消息回执和幂等重试。
- 执行显式新建会话；旧会话标记为 `closed`，但不删除其映射和审计历史。
- 服务重启后从持久化存储恢复当前活动会话。

现阶段单进程内使用 `asyncio.Lock` 做每会话串行化，SQLite 消息回执负责跨重试幂等。若未来运行多个 API 实例，应将会话锁升级为数据库租约或等价的分布式锁，不能依赖进程内锁。

### 4.2 ConversationStore

使用独立 SQLite 文件 `data/conversations.db`，启用 WAL，文件权限限制为 `0600`。建议包含以下表：

`conversation_threads`

| 字段 | 说明 |
| --- | --- |
| `conversation_id` | 应用会话 UUID，主键 |
| `home_id` / `person_id` | 当前会话归属 |
| `codex_thread_id` | Codex 返回的 thread ID，首次调用前允许为空，唯一 |
| `status` | `creating`、`active`、`closed` 或 `failed` |
| `created_at` / `last_used_at` | 创建及最后使用时间 |
| `revision` | 乐观并发控制版本 |

`message_receipts`

| 字段 | 说明 |
| --- | --- |
| `message_id` | 消息唯一 ID，主键 |
| `request_id` | 与 `message_id` 相同 |
| `conversation_id` | 所属应用会话 |
| `channel` | `console`、`voice`、`wechat` 等入口 |
| `external_message_id` | 外部平台消息 ID，可空；同渠道内唯一 |
| `status` | `processing`、`completed` 或 `failed` |
| `response_json` | 已脱敏的最终业务回执 |
| `created_at` / `completed_at` | 处理时间 |

会话状态可以更新；与状态变化对应的审计事件必须继续通过共享 `AuditRecorder` 追加写入，审计历史本身不得更新或删除。

### 4.3 CodexGateway

首次调用使用持久化模式启动 Codex：

```text
codex exec --json --ignore-user-config \
  --sandbox read-only --ask-for-approval never \
  --output-schema <purpose-schema> \
  --output-last-message <file> <prompt>
```

必须去除 `--ephemeral`，并从 JSONL 事件 `thread.started` 中读取 thread ID。只有 thread ID 成功审计并绑定到应用会话后，才允许继续可能导致设备副作用的流程。

后续调用使用：

```text
codex exec resume <exact-codex-thread-id> --json \
  --output-schema <purpose-schema> \
  --output-last-message <file> <prompt>
```

本机 Codex CLI `0.144.6` 已确认支持 `codex exec resume [SESSION_ID] [PROMPT]`、`--json`、`--output-schema` 和 `--output-last-message`。`resume` 帮助中没有重新指定 sandbox/approval 的参数，因此有以下硬约束：

- 只允许恢复由本服务按只读、禁止审批模式创建并已登记的 thread。
- 不允许恢复桌面 Codex、其他项目或客户端提供的 thread ID。
- 实现测试必须验证初始启动参数和恢复时使用的精确 ID。
- 设备控制只能通过受审计的 IOT/HA 后端调用完成，Codex 本身无直接设备控制能力。

路由、目标解析和规划可以继续使用不同的输出 schema，但都必须在同一 thread 上按顺序恢复。每次调用的脱敏后 prompt、参数、结构化输出、标准输出、标准错误和失败信息都记录到同一条消息链路。

### 4.4 消息入口适配

所有用户入口先转换为统一消息信封：

```text
message_id
request_id
channel
external_message_id?
content
```

`home_id` 和 `person_id` 由服务端入口适配器赋值，不信任客户端自行声明。微信等具有平台消息 ID 的入口，应将 `(channel, external_message_id)` 稳定映射为同一个 `message_id`，避免平台重试导致重复控制。语音转写只有在最终文本确认后才创建正式 turn，临时转写片段不能写入 thread。

系统定时任务或自主事件不默认混入人的对话 thread。本阶段只有用户入口共享活动会话；若自主事件产生了需要用户继续追问的可见结果，可以通过单独、受审计的“结果提交”turn 写入当前 thread，但不能把原始事件噪声全部灌入会话。

### 4.5 API 与前端

保留现有命令 API 的兼容行为，并新增：

- `GET /api/conversations/current`：返回当前活动 `conversation_id` 及可展示的消息历史。
- `POST /api/conversations`：显式创建并激活一个新应用会话。
- `POST /api/commands`：请求可携带当前 `conversation_id`；缺省时由服务端解析活动会话。响应始终返回实际使用的 `conversation_id`。

客户端提交的 `conversation_id` 必须属于服务端已解析出的主体；当前即 `local-home/local-user`。客户端无权传入 `codex_thread_id`。

家庭指令中心改为连续消息视图，并提供“新建会话”操作。页面历史从应用回执/审计投影视图读取，不直接读取 Codex 本地 session 文件。旧客户端不传 `conversation_id` 时仍能自动进入当前活动会话。

## 5. 消息处理流程

### 5.1 首条消息

1. 服务端生成或验证唯一 `message_id`，令 `request_id = message_id`。
2. 先写入用户请求审计并建立 `processing` 回执。
3. 获取活动会话；不存在则创建 `creating` 会话。
4. 获取会话锁，执行首次持久化 `codex exec`。
5. 从 `thread.started` 读取 ID，先审计绑定请求，再原子保存映射并记录绑定结果。
6. 在同一 thread 上依次完成路由、必要的目标解析和计划。
7. 每次准备设备操作前，重新查询 HA/IOT 的实时目录、能力、在线状态和当前状态。
8. 先审计外部调用请求，再调用设备服务；保存真实响应或失败。
9. 将脱敏后的实际执行结果通过“会话结果提交”turn 写回同一 Codex thread，生成最终用户回复。
10. 追加记录完整用户响应，完成消息回执并释放会话锁。

### 5.2 后续消息

1. 先检查 `message_id` 回执；已完成则原样返回，处理中则返回明确的处理中状态，不能重放。
2. 获取同一 `conversation_id` 的锁。
3. 按审计顺序执行 `codex exec resume <stored-id>`。
4. 即使 thread 记得设备，也重新向 HA/IOT 查询实时事实后再决定是否执行。
5. 将实际结果提交回 thread，保存最终回执并解锁。

### 5.3 结果提交

直接复用 thread 的关键是让 Codex 不仅记得用户意图，也知道真实执行结果。每条用户消息必须以一个结果提交 turn 结束：

- 普通问答的最终回答 turn 本身就是结果提交。
- 设备指令需要提交实际命中的设备、执行动作、后端返回和最新状态。
- 确认流程需要提交“等待确认”“已确认执行”或“已拒绝”的真实结果。
- 提交内容必须脱敏，且不能把凭据、内部 HTTP 头或无关诊断信息写入 thread。

这样下一轮“再关掉”“换成客厅的”“刚才失败了吗”才能基于真实结果继续，而不是基于模型曾经计划但实际未发生的动作。

## 6. 实时状态与安全边界

thread 上下文只负责语言连续性，设备事实由 HA/IOT 实时查询决定：

- 历史中设备曾在线，不代表本轮仍在线；离线设备不得执行控制。
- 历史中灯曾开启，不代表本轮仍开启；状态展示和动作结果以新查询为准。
- 历史设备已从 HA 删除时，同步逻辑删除本地对应设备，目标解析不能继续使用旧实体。
- Codex 提出的实体 ID 必须再次通过当前设备目录校验，不能直接信任历史值。

高风险确认边界保持不变：

- 用户在设备页面点击，或明确发出具体人工控制指令时，即使是门锁等高风险设备，也直接执行，不追加二次确认。
- 只有 AI 对模糊指令自主选择高风险动作，或 AI 基于事件自主决定高风险动作时，才要求二次确认。
- “设备风险高”本身不能成为人工操作需要确认的充分条件。

## 7. 并发、幂等与失败处理

### 7.1 并发顺序

同一会话一次只允许一个消息修改 thread。两个不同入口同时到达时，按服务端接受顺序排队；后一个消息必须在前一个结果提交完成后才开始。不同会话未来可并行处理。

### 7.2 失败策略

- **审计不可用**：在 Codex、MCP、HTTP 或 HA 调用前失败并阻断；不允许无审计降级执行。
- **会话库不可用**：返回会话服务不可用；禁止退化成无状态/临时 Codex 调用。
- **首次 Codex 成功但 thread ID 无法解析或保存**：不执行设备操作，记录可得的孤立 thread 信息并返回会话创建失败。
- **恢复的 thread 不存在或损坏**：返回明确的 `conversation_resume_failed`，不静默创建新 thread。提示用户显式新建会话。
- **Codex 超时或进程异常**：记录 stdout、stderr 和失败状态；相同 `message_id` 不盲目重跑可能已完成的设备动作。
- **HA 已执行但结果提交失败**：绝不重复 HA 动作；最多重试一次仅包含脱敏结果的 Codex 提交。仍失败时，最终响应以后台已知的真实设备结果为准，并附加“会话上下文同步失败”状态供下一轮和运维识别。
- **上下文窗口或 Codex session 错误**：依赖 Codex 自身 thread 管理；若恢复明确失败，当前阶段不由应用私自摘要并换 thread，必须可见失败并允许用户新建会话。

## 8. 审计设计

共享 `AuditRecorder` 增加可空的 `conversation_id`，继续支持按 `message_id` 顺序查询完整链路，并增加按 `conversation_id` 查询会话历史的投影视图。建议新增事件：

- `conversation.resolve.request` / `conversation.resolve.response`
- `conversation.created`
- `conversation.thread_bind.request` / `conversation.thread_bind.response`
- `conversation.turn.started` / `conversation.turn.completed`
- `conversation.commit.request` / `conversation.commit.response`
- `conversation.closed`
- `conversation.resume_failed`

每个 Codex 调用仍按具体 purpose 记录请求和响应；上述事件用于描述会话生命周期，不能替代 prompt、参数、结构化输出、stdout、stderr 和错误详情审计。所有落库内容先经过统一凭据脱敏器。

## 9. 迁移与兼容

- 部署后第一条新消息自动创建持久化 thread；历史 ephemeral 调用不尝试拼接或迁移。
- 不恢复用户在 Codex 桌面端或其他项目中的 thread。
- 仅本服务创建、按受限参数启动并登记的 thread 可以恢复。
- 现有 API 调用方不传 `conversation_id` 时使用服务端当前活动会话。
- 服务重启后从 SQLite 恢复映射，不创建新的 thread。
- 旧消息不回填 `conversation_id`，现有审计数据保持原样。

## 10. 测试策略

### 单元测试

- 正确解析 `thread.started` JSONL；缺失、重复或非法 ID 时失败。
- 首次调用参数不含 `--ephemeral`，且包含只读 sandbox、禁止审批、JSON 和输出 schema。
- 恢复只使用存储的精确 thread ID，永不使用 `--last`。
- 不同 purpose 在同一 thread 上使用对应 schema。
- 会话创建、绑定、关闭、重启恢复和乐观并发状态转换。
- 相同 `message_id` 返回已保存回执，不再次调用 runner 或 HA。
- 凭据在 Codex prompt、回执和审计前统一脱敏。

### 集成测试

- 首条命令绑定 thread，第二条命令恢复同一个 ID。
- 控制台、语音和微信入口都命中 `local-user` 的同一活动会话。
- 显式新建会话产生新的 `conversation_id` 和新的 Codex thread。
- API 进程重启后继续恢复原 thread。
- 两条并发消息严格按结果提交顺序进入 thread。
- thread 丢失时返回可见失败，不自动新建。
- 审计写入发生在 Codex/HA 调用之前；审计失败时 runner 和 HA 均未调用。
- HA 已执行但结果提交失败时不重复设备动作。
- 每个链路事件的 `request_id == message_id`，且可以按 `message_id` 和 `conversation_id` 还原顺序。

### 端到端场景

- 用户说“打开书房灯”，随后从另一入口说“再暗一点”；第二句恢复同一 thread，并基于灯的实时亮度执行。
- 用户控制后设备被其他系统修改；下一轮以 HA 最新状态为准，而不是以 thread 历史为准。
- 同一个平台消息重投两次，只产生一次设备副作用。
- 明确人工控制高风险设备不触发确认；AI 对模糊目标自主选择高风险设备时触发确认。
- 用户新建会话后，省略指代不再继承旧 thread 的上下文。

默认测试使用可控的假 Codex runner 和假 HA provider，不访问真实设备。真实 Codex thread 的本地 smoke test必须显式启用、具有完整审计，并且不包含设备副作用。

## 11. 非目标

本阶段不包括：

- 多用户身份识别和权限模型。
- 多家庭、共享家庭成员或不同用户之间的会话隔离 UI。
- 自动摘要、闲置超时或自动创建新会话。
- 将任意桌面 Codex thread 导入家庭指令中心。
- 在应用数据库复制完整 Codex session 内容。
- 对话流式输出、语音识别和微信平台本身的接入实现。

## 12. 验收标准

1. 连续两条命令在进程重启前后都能恢复同一个 Codex thread。
2. 当前所有用户入口默认共享 `local-home/local-user` 的活动会话。
3. 用户显式新建会话后才切换到新的 thread，没有闲置自动切换。
4. thread 历史能够解决自然语言上下文，但任何设备执行都使用 HA/IOT 的最新事实。
5. 重复消息、并发消息、thread 丢失和结果提交失败均不会导致重复设备副作用。
6. 全链路保留一致的 `message_id/request_id/conversation_id`，外部副作用前有成功请求审计，所有凭据已脱敏。
7. 人工明确控制与 AI 自主高风险决策继续遵守既有确认边界。
