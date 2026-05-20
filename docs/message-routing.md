# 消息路由与原渠道响应设计

本文档细化消息模块。目标是保证“消息从什么渠道来，就优先从什么渠道返回”，同时不牺牲隐私、安全、确认和审计边界。

核心结论：

- 原渠道响应是默认策略，不是绝对规则。
- 入站消息必须保存可恢复的 `RouteRef`，输出时才能准确回到本地 PWA、本地 API、微信、钉钉、飞书、语音或本地屏幕。
- `Notification Policy` 负责决定能不能原路返回；`Outbound Dispatcher` 负责渲染、发送、重试、降级和审计。
- 群聊、语音、触摸屏、Camera、IoT、Scheduler 等来源的“返回目标”语义不同，不能只用 `channel` 一个字段表达。

## 1. 模块边界

消息模块分成入站和出站两部分：

```text
Channel Adapter
  -> Relay / UnifiedMessage / RouteRef
  -> Application Orchestrator
  -> Codex / Tool / Task / Confirmation
  -> OutputEnvelope
  -> Notification Policy
  -> MessageEnvelope
  -> Outbound Dispatcher
  -> Channel Adapter
  -> DeliveryAttempt / AuditEvent
```

职责划分：

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| 入站 Adapter | 接收平台事件，保留平台原始路由信息 | 不判断业务权限，不调用 Codex |
| Relay | 标准化为 `UnifiedMessage`，去重，写入站事件 | 不决定最终回复目标 |
| Notification Policy | 根据来源、敏感度、风险、身份和家庭模式决定投递目标 | 不直接调用平台 SDK |
| Outbound Dispatcher | 渲染、发送、重试、fallback、记录投递结果 | 不绕过通知策略 |
| Channel Adapter | 封装微信、钉钉、飞书、语音、屏幕等协议差异 | 不拥有用户权限逻辑 |

## 2. 路由原则

### 2.1 原渠道优先

普通低风险、低敏感度、身份明确的输出默认回到原渠道：

- 微信单聊进来，微信单聊回复。
- 飞书群聊进来，飞书群聊回复摘要。
- 钉钉单聊进来，钉钉单聊回复。
- 本地屏幕点击进来，在对应屏幕展示结果。
- 语音低风险请求进来，可以在本地语音短播报。

### 2.2 原渠道不是安全豁免

以下情况必须改道、降级或静默：

- 群聊中涉及个人记忆、身份、权限、Camera、门锁、人在家状态。
- 高风险动作需要确认，例如门锁、燃气、摄像头隐私、身份合并、自动化创建。
- 语音来源身份置信度不足，或语音输出会被旁人听到。
- 本地 Pad 是公共屏幕，不能展示私人结果。
- 原渠道不支持确认按钮、长文本、图片、文件、语音或引用回复。
- 原渠道 token 过期、机器人被移出群、发送失败或平台限流。

### 2.3 没有人类来源的事件

IoT、Camera、Scheduler 不是“可回复会话”。它们的输出目标由任务、订阅和家庭规则决定：

- IoT 事件：通知设备 owner、房间负责人、订阅者或静默记录。
- Camera 事件：默认私聊有权限的人，公共屏幕只显示低敏摘要。
- Scheduler：通知任务创建者、订阅者或 eligible approvers。
- 系统告警：通知 owner/admin，紧急事件可多渠道升级。

## 3. 核心数据契约

### 3.1 RouteRef

`RouteRef` 是原渠道响应的事实依据。它保存“如何回到这次输入来源”，但不代表一定允许回去。

```python
RouteRef = {
    "route_id": "string",
    "home_id": "string",
    "channel": "wechat|dingtalk|lark|voice|screen|iot|camera|scheduler|http_mock",
    "adapter_instance_id": "string",
    "tenant_id": "string|null",
    "bot_id": "string|null",
    "platform_user_id": "string|null",
    "platform_conversation_id": "string|null",
    "conversation_type": "dm|group|room|surface|system|null",
    "thread_id": "string|null",
    "reply_to_message_id": "string|null",
    "surface_id": "string|null",
    "voice_zone_id": "string|null",
    "capabilities": ["text", "markdown", "image", "voice", "buttons", "thread_reply"],
    "expires_at": "datetime|null",
    "created_at": "datetime"
}
```

设计要点：

- `RouteRef` 由入站 Adapter 生成，Relay 持久化。
- 平台会话、群聊、thread、reply_to、机器人实例、租户必须保留，避免“回错群”。
- `capabilities` 用于判断是否能承载确认按钮、图片、长文本、语音等输出。
- `expires_at` 用于处理平台临时回复窗口、语音会话窗口、屏幕 session。

### 3.2 UnifiedMessage 扩展

`UnifiedMessage` 应携带入站路由引用：

```python
UnifiedMessage = {
    "message_id": "string",
    "trace_id": "string",
    "home_id": "string",
    "source_event_id": "string",
    "route_ref": RouteRef,
    "actor_hint": {},
    "content": {},
    "content_type": "text|voice|image|camera_event|iot_event|scheduler_event|ui_event",
    "trust_level": "trusted_context|user_instruction|weak_user_instruction|untrusted_content",
    "provenance": {},
    "received_at": "datetime"
}
```

### 3.3 OutputEnvelope 扩展

`OutputEnvelope` 是上游模块交给输出层的结构化结果。

```python
OutputEnvelope = {
    "output_id": "string",
    "trace_id": "string",
    "home_id": "string",
    "source_event_id": "string|null",
    "origin_route_id": "string|null",
    "response_type": "chat|iot_result|suggestion|confirmation|camera_result|task_result|system_alert|silent",
    "sensitivity": "public|household|private|admin_only|secret",
    "risk_level": "low|medium|high|admin|null",
    "correlation": {
        "task_id": "string|null",
        "confirmation_id": "string|null",
        "operation_id": "string|null",
        "tool_invocation_id": "string|null"
    },
    "content": {},
    "delivery_intent": "reply_to_source|notify_actor|notify_subscribers|confirm_approvers|broadcast|silent",
    "created_at": "datetime"
}
```

`origin_route_id` 用于保持原渠道亲和；`delivery_intent` 用于表达业务意图，但最终目标仍由 `Notification Policy` 决定。

### 3.4 MessageEnvelope

`MessageEnvelope` 是可以交给出站调度器执行的消息。

```python
MessageEnvelope = {
    "message_envelope_id": "string",
    "output_id": "string",
    "trace_id": "string",
    "home_id": "string",
    "response_type": "chat|iot_result|suggestion|confirmation|camera_result|task_result|system_alert",
    "sensitivity": "public|household|private|admin_only|secret",
    "target": {},
    "content": {},
    "render_policy": {},
    "delivery_policy": {
        "priority": "low|normal|high|urgent",
        "retry": {},
        "fallback": {},
        "expires_at": "datetime|null"
    },
    "idempotency_key": "string",
    "created_at": "datetime"
}
```

### 3.5 ReplyTarget

```python
ReplyTarget = {
    "mode": "source|dm|group|voice|screen|multi|silent",
    "channel": "wechat|dingtalk|lark|voice|screen|system",
    "conversation_type": "dm|group|room|surface|system|null",
    "conversation_id": "string|null",
    "recipient_person_id": "string|null",
    "platform_user_id": "string|null",
    "reply_to_message_id": "string|null",
    "thread_id": "string|null",
    "surface_id": "string|null",
    "is_original_route": True,
    "capabilities": ["text", "markdown", "image", "voice", "buttons", "thread_reply"],
    "fallback_targets": []
}
```

`recipient_person_id` 和 `platform_user_id` 必须分开：前者是自然人，后者是某个平台账号。

### 3.6 DeliveryAttempt

```python
DeliveryAttempt = {
    "delivery_attempt_id": "string",
    "message_envelope_id": "string",
    "output_id": "string",
    "trace_id": "string",
    "home_id": "string",
    "channel": "string",
    "target": {},
    "attempt_no": 1,
    "idempotency_key": "string",
    "adapter_message_id": "string|null",
    "status": "pending|sending|succeeded|retryable_failed|permanent_failed|cancelled|suppressed",
    "error_code": "string|null",
    "error_message": "string|null",
    "next_retry_at": "datetime|null",
    "sent_at": "datetime|null",
    "created_at": "datetime"
}
```

## 4. NotificationDecision

通知策略的输出建议扩展为：

```python
NotificationDecision = {
    "decision_id": "string",
    "trace_id": "string",
    "output_id": "string",
    "decision": "deliver|private_redirect|group_summary|silent|defer|escalate|reject",
    "primary_target": ReplyTarget,
    "fallback_targets": [ReplyTarget],
    "redaction_policy": {},
    "reason_codes": [],
    "expires_at": "datetime|null",
    "created_at": "datetime"
}
```

原因码：

- `source_route_affinity`
- `sensitive_private_redirect`
- `group_summary_only`
- `voice_public_surface`
- `high_risk_confirmation_required`
- `identity_uncertain`
- `unsupported_channel_capability`
- `route_expired`
- `delivery_fallback`
- `recipient_not_authorized`
- `silent_low_value_event`

## 5. 渠道策略矩阵

| 来源 | 普通低敏回复 | 敏感内容 | 高风险确认 | 失败降级 |
| --- | --- | --- | --- | --- |
| 微信单聊 | 微信原会话 | 微信原会话或用户首选私聊 | 绑定的个人渠道确认 | 其他已验证个人渠道 |
| 微信群聊 | 群聊摘要 | 私聊相关成员，群里只提示已私聊 | 私聊 eligible approvers | owner/admin 渠道 |
| 飞书单聊 | 飞书原会话 | 飞书原会话或个人渠道 | 私聊确认 | 其他已验证个人渠道 |
| 飞书群聊 | 群聊摘要，更偏保守 | 私聊，不暴露家庭细节 | 私聊确认 | owner/admin 渠道 |
| 钉钉单聊 | 钉钉原会话 | 钉钉原会话或个人渠道 | 私聊确认 | 其他已验证个人渠道 |
| 钉钉群聊 | 群聊摘要，更偏保守 | 私聊 | 私聊确认 | owner/admin 渠道 |
| 家庭语音 | 短语音播报 | 转个人私聊或屏幕隐去详情 | 手机确认，不在公共语音确认 | 屏幕低敏提示或私聊 |
| 本地屏幕 | 屏幕展示低敏结果 | 私密交接到手机 | 公共屏不可直接批准 | 私聊、静默或 owner |
| Camera 事件 | 通常不原路回复 | 私聊有权限成员 | 不适用或审批后查看 | owner/admin |
| IoT 事件 | 通常不原路回复 | 按订阅/权限 | 自动化建议需确认 | owner/admin |
| Scheduler | 通知创建者/订阅者 | 按任务可见性 | eligible approvers | 延迟、静默或 owner |

## 6. Fallback 规则

默认 fallback 顺序：

1. 原 `RouteRef`。
2. 同平台私聊。
3. 用户首选且已验证的个人渠道。
4. eligible approver 或 owner 的已验证渠道。
5. 本地屏幕或语音只展示低敏摘要。
6. 延迟、静默、拒绝或等待用户绑定渠道。

硬边界：

- 私人内容不能 fallback 到群聊。
- 高风险确认不能 fallback 到未验证渠道。
- 语音播报视为公共输出，不能播报敏感详情。
- 屏幕点击不是自然人身份，除非有额外认证。
- fallback 不改变原始权限判断，也不能降低风险等级。

## 7. ChannelAdapter 接口

```python
class ChannelAdapter:
    channel: str

    def capabilities(self) -> dict:
        ...

    def validate_target(self, target: ReplyTarget) -> None:
        ...

    def render(self, message: MessageEnvelope) -> dict:
        ...

    async def send(self, rendered_payload: dict, *, idempotency_key: str) -> dict:
        ...
```

Adapter 需要返回平台消息 ID、错误码、是否可重试、限流信息和可见文本摘要。出站调度器把这些结果写入 `DeliveryAttempt` 和 `AuditEvent`。

## 8. 幂等与错误处理

三层幂等：

| 层级 | Key | 目的 |
| --- | --- | --- |
| 入站 | `source_event_id + adapter_instance_id` | 平台重试不重复处理 |
| 输出 | `output_id + target_hash + content_hash` | 同一输出不重复创建消息 |
| 投递 | `message_envelope_id + channel + target + attempt_no` | 平台发送重试可追踪 |

错误类型：

- `RetryableDeliveryError`：网络、限流、平台临时错误。
- `PermanentDeliveryError`：机器人无权限、用户不存在、会话不可达。
- `PolicyBlockedError`：策略禁止发到该目标。
- `AuditUnavailableError`：审计不可用，阻断高风险或敏感投递。

## 9. 审计事件

消息模块至少记录：

- `route_ref_created`
- `output_envelope_created`
- `notification_decision_made`
- `message_envelope_created`
- `message_rendered`
- `delivery_attempt_started`
- `delivery_attempt_succeeded`
- `delivery_attempt_failed`
- `delivery_fallback_selected`
- `delivery_suppressed`
- `private_redirect_performed`

每个事件都应包含 `trace_id`、`home_id`、`person_id`、`channel`、`target_ref`、`sensitivity`、`risk_level`、`reason_codes`。

## 10. 第一期实现范围

第一期建议实现：

- `RouteRef`、`ReplyTarget`、`MessageEnvelope`、`DeliveryAttempt` 表。
- local PWA、local API、scheduler/local timer、HTTP/mock、voice mock、camera mock、screen mock adapter。
- `source_route_affinity` 策略：低敏低风险默认回到本地 PWA 或原入口。
- 本地 owner 私有输出、公共屏低敏摘要、静默记录和审计。
- 高风险确认投递到本地确认页，第一期不依赖跨平台按钮。
- 投递重试、静默和本地 fallback；跨平台 fallback 后置。

E2E 样例：

- 本地 PWA“把客厅灯调暗”后在 PWA 展示执行结果和 trace。
- local API 创建晚上 10 点提醒后，本地 PWA 显示提醒。
- 微信单聊“把客厅灯调暗”后微信单聊回复。
- 飞书群聊问 Camera 结果，群聊只回摘要，详情私聊。
- 家庭语音请求开门，手机确认，不在语音中批准。
- 原渠道发送失败，fallback 到用户已验证私聊渠道。
- 平台重复 webhook 不重复回复。
