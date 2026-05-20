# 可视化触摸屏与家庭屏幕设计

本文档细化第一期本地 PWA/kiosk，以及长期家庭 Pad、墙面触摸屏、入口屏、私有平板等可视化模块。目标是把屏幕设计成 Home Assist Agent 的安全交互入口，而不是另一个完整 Home Assistant Dashboard。

设计参考：

- Home Assistant Dashboard 的卡片、视图、条件可见和设备控制模型。
- openHAB Overview / Pages 强调“首页只放最重要控制和上下文入口”的思路。
- MagicMirror 的常驻低打扰信息屏模式，例如时钟、天气、日历、新闻和提醒模块。

本项目的屏幕模块要服务于 Agent：展示当前家庭助理认为需要看见、确认或跟进的内容，并把触摸事件重新送回统一消息链路。

## 1. 设计定位

屏幕不是身份。

公共 Pad、墙面屏、客厅屏即使有人点击，也不能自动认为点击者就是某个家庭成员。屏幕只能提供：

- 所在位置和设备身份。
- 当前屏幕 session。
- 可能的操作者证据，例如 PIN、手机在场、NFC、语音、扫码确认。
- 触摸、表单、语音、导航等 UI 事件。

自然人身份仍由 `Identity Resolver` 和 `Policy Engine` 判定。

## 2. 架构链路

```text
Display Surface / Browser Kiosk / Pad
  <-> Display Gateway (WebSocket/SSE)
  -> UIEvent Normalizer
  -> Relay / UnifiedMessage
  -> Identity / Policy / Context
  -> Codex / Task / Confirmation
  -> Tool Safety Proxy
  -> Notification Policy
  -> Screen Policy
  -> Display Renderer
  -> Display Surface
```

关键边界：

- 屏幕不直接连接 Home Assistant WebSocket，不直接调用 HA 服务。
- 屏幕上的低风险控制也必须变成 `UIEvent -> UnifiedMessage -> Tool Safety Proxy`。
- 屏幕展示由 `Screen Policy` 决定，不能直接渲染 Codex 原始文本或敏感工具结果。
- 所有 UI 操作写入 `AuditEvent`。

## 3. 核心模型

### 3.1 DisplaySurface

```python
DisplaySurface = {
    "surface_id": "string",
    "home_id": "string",
    "device_identity_id": "string",
    "name": "客厅 Pad",
    "area_id": "living_room",
    "surface_type": "shared_pad|wall_panel|entry_panel|private_pad|admin_console",
    "audience_mode": "public|semi_private|private|admin",
    "capabilities": ["touch", "voice", "speaker", "camera", "qr", "wake_lock", "haptics"],
    "auth_capabilities": ["none", "pin", "phone_handoff", "nfc", "biometric", "admin_login"],
    "default_view_profile": "ambient|control|personal|admin",
    "privacy_zone": "public_area|family_area|bedroom|entry|private_room",
    "policy_profile_id": "string",
    "status": "online|offline|suspended|revoked",
    "last_seen_at": "datetime"
}
```

### 3.2 ScreenSession

```python
ScreenSession = {
    "screen_session_id": "string",
    "surface_id": "string",
    "home_id": "string",
    "active_person_id": "string|null",
    "identity_confidence": "none|low|medium|high|verified",
    "auth_method": "none|pin|phone_handoff|nfc|voice|biometric|admin_login",
    "view_id": "ambient|room|assistant|task|confirm|admin|private_handoff",
    "privacy_mode": "ambient|focused|locked|private",
    "connection_id": "string",
    "resume_cursor": "string|null",
    "started_at": "datetime",
    "last_active_at": "datetime"
}
```

### 3.3 UIEvent

```python
UIEvent = {
    "event_id": "string",
    "trace_id": "string",
    "surface_id": "string",
    "screen_session_id": "string",
    "event_type": "tap|form_submit|voice_request|confirm_click|navigate|heartbeat|presence",
    "component_id": "string",
    "payload": {},
    "actor_evidence": {},
    "trust_level": "trusted_context|user_instruction|weak_user_instruction|untrusted_content",
    "action_token": "string|null",
    "idempotency_key": "string",
    "created_at": "datetime"
}
```

### 3.4 VisualDecision

```python
VisualDecision = {
    "decision_id": "string",
    "trace_id": "string",
    "home_id": "string",
    "decision": "display|redact_display|private_handoff|confirm|silent|defer|escalate",
    "target_surfaces": ["surface_id"],
    "card_type": "home_brief|attention|room|control|task|confirmation|camera_event|ha_result|privacy|audit_mini|system_alert",
    "priority": "p0_critical|p1_action_required|p2_time_sensitive|p3_routine|p4_silent",
    "sensitivity": "public|household|private|admin_only|secret",
    "ttl_seconds": 300,
    "required_auth": "none|pin|phone_handoff|nfc|biometric|admin",
    "allowed_interactions": ["view", "dismiss", "open", "low_risk_control", "handoff_to_phone"],
    "redaction_policy": {},
    "reason_codes": [],
    "created_at": "datetime"
}
```

### 3.5 VisualCard

第一期内置卡片：

| 卡片 | 用途 | 默认交互 |
| --- | --- | --- |
| `home_brief` | 时间、天气、空气、家庭模式、系统健康摘要 | 查看、展开 |
| `attention` | 需要注意的事情，例如门没关、设备离线、任务等待 | 查看、处理、稍后 |
| `room` | 当前房间低敏设备概览 | 查看、低风险控制 |
| `control` | 场景和低风险控制，例如灯光、窗帘、空调建议 | 点击、滑块、撤销 |
| `task` | 家庭共享任务、提醒、待办 | 完成、稍后、转手机 |
| `confirmation` | 确认请求摘要 | 公共屏仅转手机/取消；私有屏可按权限确认 |
| `camera_event` | Camera 事件摘要 | 私密交接，公共屏不展示人物细节 |
| `ha_result` | 设备控制结果 | 查看结果、撤销低风险动作 |
| `privacy` | 隐私模式、屏幕锁、访客模式 | 切换、说明 |
| `audit_mini` | 最近低敏操作摘要 | 查看简略记录 |
| `system_alert` | HA、平台、Agent 异常 | 查看、通知 owner |

## 4. 默认状态

### 4.1 Ambient 模式

无人交互或屏幕空闲时默认进入 `ambient`：

- 低亮度。
- 大号时间、日期、天气、空气质量。
- 家庭模式，例如在家、睡眠、访客、离家。
- 低敏设备摘要，例如“3 盏灯开着”，不显示卧室、Camera、人在家细节。
- 当前房间最常用低风险场景。
- Agent 健康状态：在线、HA 连接、麦克风状态。
- 不展示私人日程、个人消息、Camera 图片、身份绑定状态。

### 4.2 Focused 模式

有人触摸、唤醒词、靠近或扫码后进入 `focused`：

- 展示当前房间控制和家庭共享任务。
- 支持低风险控制。
- 对中风险动作展示参数和二次确认。
- 对高风险动作提示转手机确认。
- 一段时间无操作后回到 `ambient`。

### 4.3 Private 模式

已通过 PIN、手机交接、NFC、私有 Pad 登录等方式确认身份时进入 `private`：

- 可展示该人的个人提醒、私有任务和允许的私密结果。
- 仍然按权限控制设备和记忆。
- 到期、离开或锁屏后清空私密内容。

### 4.4 Locked / Degraded 模式

网络、HA、Agent 或审计不可用时：

- 不执行有副作用动作。
- 展示系统不可用摘要。
- 保留本地只读信息和低敏缓存。
- 高风险、敏感和确认统一转手机或等待恢复。

## 5. 屏幕类型

| 类型 | 位置 | 默认模式 | 可做 | 不能做 |
| --- | --- | --- | --- | --- |
| 客厅共享 Pad | 公共区域 | ambient/control | 家庭摘要、低风险场景、共享任务 | 展示个人隐私、高风险批准 |
| 墙面屏 | 走廊/厨房 | ambient/control | 时间天气、提醒、房间控制 | 长文本输入、敏感详情 |
| 入口屏 | 门口 | ambient/locked | 门铃摘要、访客提示、天气、离家检查 | 门锁直接解锁、Camera 细节 |
| 私有 Pad | 卧室/个人设备 | personal | 个人提醒、私密交互、确认部分中风险动作 | 越权控制其他成员资源 |
| 管理屏 | 机房/书房 | admin | 系统健康、日志摘要、设备状态 | 未授权查看家庭成员隐私 raw 内容 |

## 6. 场景决策

| 场景 | 公共屏行为 | 私有屏行为 | 手机/IM 行为 |
| --- | --- | --- | --- |
| 低风险灯光控制成功 | 显示“已调整客厅灯” | 同左 | 原渠道回复 |
| 门锁/燃气/高功率设备 | 显示“需要手机确认” | 可显示摘要，仍需强认证 | 向 eligible approver 发确认 |
| Camera 识别到人 | 只显示“有一条摄像头事件” | 有权限可看摘要 | 私聊有权限成员 |
| 群聊请求个人记忆 | 不展示 | 仅本人私有屏可展示 | 私聊本人 |
| 家庭共享提醒到期 | 显示简短提醒 | 显示详情 | 通知创建者/订阅者 |
| 系统故障 | 显示低敏告警 | 显示更详细摘要 | 通知 owner/admin |
| 夜间非紧急提醒 | 不亮屏或低亮度 | 低打扰 | 延迟或静默 |
| 紧急安全事件 | 高优先级显著提醒 | 显著提醒 | 多渠道升级 |

## 7. Screen Policy

`Screen Policy` 位于 `Notification Policy` 之后，负责把一个可视化输出映射到具体屏幕和卡片。

输入：

- `OutputEnvelope`
- `NotificationDecision`
- `DisplaySurface`
- `ScreenSession`
- `ActorContext`
- `HomeMembership`
- `home_mode`
- `sensitivity`
- `risk_level`

输出：

- `display`：直接显示。
- `redact_display`：显示摘要，隐藏细节。
- `private_handoff`：展示二维码、手机推送或“已发送到手机”。
- `confirm`：显示确认 UI，但需满足认证和权限。
- `silent`：不显示，只记日志。
- `defer`：延迟到合适时间或模式。
- `escalate`：升级到 owner/admin 或多渠道。

规则：

- 公共屏默认最多展示 `public` 和部分 `household` 内容。
- `private`、`admin_only`、`secret` 默认转手机或私有屏。
- 高风险确认不在公共屏直接批准。
- 低风险控制可在公共屏执行，但仍走 `Tool Safety Proxy`。
- 任何屏幕展示敏感内容前都要检查 session 认证强度和过期时间。

## 8. 确认设计

屏幕上的确认必须绑定动作摘要和 hash：

```python
ScreenActionToken = {
    "token_id": "string",
    "trace_id": "string",
    "surface_id": "string",
    "screen_session_id": "string",
    "action_plan_hash": "string",
    "allowed_action": "dismiss|handoff|approve_low_risk|cancel",
    "expires_at": "datetime",
    "created_at": "datetime"
}
```

批准前置条件：

1. `ConfirmationRequest` 仍处于 pending。
2. 当前操作者身份明确且有权限。
3. 屏幕 session 的认证强度满足 `required_auth`。
4. `action_plan_hash` 与待执行动作一致。
5. 执行前重新进入 `Tool Safety Proxy`，重新查询 HA 当前状态。

公共屏默认只允许：

- 查看摘要。
- 取消自己发起的低风险请求。
- 发送到手机确认。
- 呼叫 owner/admin。

## 9. 多屏协同

多处 Pad 或触摸屏同时存在时：

- 按区域优先：厨房事件优先厨房屏，入口事件优先入口屏。
- 按隐私优先：公共屏只能摘要，私有屏或手机承载详情。
- 按优先级同步：P0 紧急事件可多屏广播，P3 日常事件只显示在相关区域。
- 按状态去重：一个任务被某屏处理后，其他屏要撤卡或更新状态。
- 按 lease 控制：表单编辑、确认、任务处理要避免多屏重复提交。

优先级：

| 优先级 | 说明 | 示例 |
| --- | --- | --- |
| P0 critical | 安全紧急，可打断 | 烟雾、漏水、门异常 |
| P1 action_required | 需要人工处理 | 确认、设备异常、访客等待 |
| P2 time_sensitive | 有时间窗口 | 出门提醒、饭点提醒 |
| P3 routine | 日常信息 | 天气、待办、设备结果 |
| P4 silent | 不打扰 | 重复事件、低价值状态 |

## 10. 与 HA Dashboard 的关系

本项目不替代 Home Assistant Dashboard。推荐分工：

- HA Dashboard：面向设备管理、实体调试、自动化配置。
- Home Assist Agent 屏幕：面向家庭成员、任务、确认、建议、低风险场景、Agent 结果。

屏幕可以显示 HA 状态摘要，但不直接暴露 HA WebSocket 或服务调用给前端。所有控制都进入本项目链路。

## 11. 通信协议

推荐：

- WebSocket：双向实时交互，适合 Pad、触摸屏、确认、任务状态同步。
- SSE：只读或低交互屏幕，适合常驻状态推送。
- HTTP：启动配置、静态资源、设备配对、截图/缩略图受控下载。

不要让屏幕直接连 Home Assistant WebSocket。屏幕连接 `Display Gateway`，由后端完成鉴权、脱敏、策略和审计。

## 12. 审计事件

可视化模块至少记录：

- `display_surface_registered`
- `display_surface_revoked`
- `screen_session_started`
- `screen_session_authenticated`
- `screen_policy_decision`
- `screen_output_rendered`
- `screen_output_redacted`
- `ui_event_received`
- `screen_action_token_issued`
- `local_confirmation_presented`
- `local_confirmation_decided`
- `screen_connection_lost`

## 13. 第一期实现范围

第一期建议实现：

- 一个本地 PWA/kiosk，作为第一期核心产品入口，而不只是附属屏幕。
- `DisplayGateway` WebSocket。
- `DisplaySurface`、`ScreenSession`、`UIEvent`、`VisualDecision`、`VisualCard` 基础表。
- home 首页：时间、天气、家庭模式、Agent/HA 健康、今日提醒、待确认事项、最近执行记录。
- room 页：当前房间低风险灯光/场景控制。
- assistant overlay：展示 Agent 回复和任务状态。
- confirmation queue：本地确认页处理低风险/中风险确认；公共屏只能取消低风险请求或转私密处理。
- memory 页：查看、修改、删除个人偏好和家庭规则草案。
- trace replay 页：展示输入、身份、策略、HA 查询、工具调用、输出和审计事件。
- showcase mode：加载 demo 数据、决策卡片、mock 门锁/camera/OCR 注入和重置按钮。
- 完整审计和 E2E：本地 PWA 开灯、Camera 事件私密交接、高风险门锁 dry-run。

## 14. 参考资料

- [Home Assistant Dashboards](https://www.home-assistant.io/dashboards/)
- [Home Assistant Dashboard cards](https://www.home-assistant.io/dashboards/cards/)
- [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket/)
- [openHAB User Interface Design Overview](https://www.openhab.org/docs/ui/)
- [MagicMirror² module introduction](https://docs.magicmirror.builders/modules/introduction.html)
- [MDN WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)
- [MDN Server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
