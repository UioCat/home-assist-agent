# 通知、审计与运行保障设计

本文档聚焦 Home Assist Agent 的输出层、`Notification Policy`、`Audit Log`、可观测性、离线降级恢复、数据存储、Python 工程结构和 MVP 落地顺序。

更细的模块设计已拆到独立文档：原渠道回复、fallback 和投递幂等见 [消息路由与原渠道响应](message-routing.md)；按人/模块/trace 排查、日志模型和脱敏策略见 [日志、审计与可观测性](logging-observability.md)；Pad 和触摸屏展示策略见 [可视化触摸屏与家庭屏幕](visual-surfaces.md)。

边界原则：

- Codex 可以生成回复、动作意图、任务建议、确认请求和记忆候选，但不能决定最终发给谁、发到哪里、以什么方式打扰用户。
- 所有输出必须经过 `Notification Policy`；所有真实世界副作用必须经过 `Tool Safety Proxy`。
- `Audit Log` 是跨模块 append-only ledger，不是输出后的普通日志。
- 高风险动作、敏感信息、跨用户隐私和系统不完整状态下的执行结果，默认走私聊、确认、拒绝或静默记录。
- 可观测性从第一阶段就进入 MVP，否则后续很难解释一次家庭助理行为为什么发生。

## 1. 输出层职责

输出层负责把上游模块产生的结构化结果投递到正确目标，并记录完整审计。它不是简单转发 Codex 文本，而是把 `CodexResult`、`ToolInvocation`、`ConfirmationRequest`、`TaskRun`、Camera 查询结果和系统告警转换为可投递消息。

输出类型：

| 类型 | 来源 | 默认投递 |
| --- | --- | --- |
| 普通聊天回复 | Codex 自然语言结果 | 原会话回复 |
| IoT 控制结果 | Tool Safety Proxy 执行结果 | 原路回复，必要时私聊操作者 |
| IoT 建议 | Codex 或策略层建议 | 原路回复或私聊确认 |
| 确认请求 | Confirmation Broker | 私聊 eligible approvers，必要时原会话提示已转私聊 |
| Camera 查询结果 | Camera 事件或检索工具 | 私聊请求者，群聊只发摘要 |
| 定时任务结果 | Task Orchestrator / TaskRun | 创建者、订阅者或静默记录 |
| 系统告警 | Worker、存储、HA、平台适配器 | 管理员、owner 或本地语音告警 |
| 静默记录 | 低价值事件、重复事件、已处理事件 | 不打扰用户，只写审计和指标 |

每个输出必须携带：

```python
OutputEnvelope = {
    "output_id": "string",
    "trace_id": "string",
    "home_id": "string",
    "source_event_id": "string|null",
    "target": {},
    "response_type": "chat|iot_result|suggestion|confirmation|camera_result|task_result|system_alert|silent",
    "requires_confirmation": False,
    "sensitivity": "public|household|private|admin_only|secret",
    "delivery_policy": {},
    "audit_record_id": "string|null",
    "created_at": "datetime"
}
```

输出层需要做到：

- 支持原平台、群聊、私聊、语音播报、多平台通知和静默。
- 保留用户看到的最终文案、结构化目标和投递结果。
- 对投递失败执行重试、降级或补偿通知。
- 不把私密内容发到群聊，不把确认 token 暴露给无权限用户。
- 不因 Codex 文本中出现“发给所有人”而绕过通知策略。

## 2. Notification Policy

`Notification Policy` 决定输出目标、渠道、打扰等级、敏感内容处理和失败降级。

输入信号：

| 信号 | 说明 |
| --- | --- |
| `trace_id` / `home_id` | 绑定一次请求和家庭边界 |
| `actor_context` | 请求者、角色、权限、身份置信度 |
| `source` / `conversation` | 平台、单聊、群聊、语音、本地事件 |
| `risk_level` | low、medium、high、admin |
| `sensitivity` | public、household、private、admin_only、secret |
| `trust_level` | trusted_context、user_instruction、weak_user_instruction、untrusted_content |
| `home_mode` | 睡眠、勿扰、离家、在家、访客模式 |
| `user_preferences` | 静默、语音播报、只发手机、夜间勿扰 |
| `delivery_history` | 是否已经由其他渠道投递或由其他人处理 |
| `task_context` | 任务创建者、订阅者、eligible approvers、过期时间 |

基础决策：

| 场景 | 策略 |
| --- | --- |
| 单聊普通问答 | 原路回复 |
| 群聊普通问答 | 群聊回复必要摘要 |
| 群聊中涉及个人记忆、身份、权限、Camera、门锁、人在家状态 | 群聊提示“已私聊处理”，详情转私聊 |
| 低风险 IoT 成功 | 原路回复简短结果 |
| 高风险 IoT | 不在群聊直接确认，向有资格审批人私聊确认 |
| 身份不确定 | 只做低风险澄清，不投递敏感内容 |
| 设备离线或 HA 不可用 | 通知请求者失败原因，必要时告警 owner |
| 紧急安全事件 | 升级到多渠道通知，可绕过普通勿扰但仍保留审计 |
| 重复或低价值事件 | 静默记录或合并摘要 |
| 夜间非紧急事件 | 延迟、静默或只发低打扰渠道 |

通知策略输出：

```python
NotificationDecision = {
    "decision_id": "string",
    "trace_id": "string",
    "output_id": "string",
    "decision": "deliver|private_redirect|group_summary|silent|defer|escalate|reject",
    "channels": ["source", "wechat_dm", "lark_dm", "local_voice"],
    "recipients": [],
    "redaction_policy": {},
    "reason_codes": [],
    "expires_at": "datetime|null",
    "created_at": "datetime"
}
```

策略原因码建议：

- `same_conversation_reply`
- `sensitive_private_redirect`
- `high_risk_confirmation_required`
- `identity_uncertain`
- `home_mode_do_not_disturb`
- `emergency_escalation`
- `duplicate_suppressed`
- `delivery_fallback`
- `recipient_not_authorized`
- `system_degraded`

## 3. 敏感内容私聊

敏感内容默认私聊，群聊只保留必要的非敏感提示。群聊里即使发起人身份明确，也可能存在旁观者、陌生成员、机器人或截图传播风险。

必须私聊的内容：

| 内容 | 原因 |
| --- | --- |
| 个人记忆、偏好、日程、健康、财务、身份绑定状态 | 属于个人私密上下文 |
| 家庭权限、角色、访客授权、身份合并细节 | 可能暴露家庭安全边界 |
| 门锁、摄像头、人在家状态、儿童房或卧室信息 | 家庭安全和隐私敏感 |
| Camera 截图、视频片段、访客识别、历史检索结果 | 包含可识别个人和空间信息 |
| 高风险动作确认链接或确认口令 | 不能被无权限成员复用 |
| 审计详情、错误堆栈、系统 token、内部 trace 细节 | 只给管理员或开发调试环境 |

群聊处理方式：

- 对普通敏感结果：群聊回复“这部分包含个人或家庭敏感信息，我已私聊相关成员。”
- 对高风险确认：群聊回复“这个操作需要私聊确认后才能继续。”
- 对无权限请求：群聊只回复拒绝摘要，不说明完整权限配置。
- 对 Camera 结果：群聊可回复“已找到相关时间段”，截图、片段和人物细节转私聊。
- 对身份不确定的语音或群聊发言：先澄清身份，不私聊错误对象。

私聊前置校验：

1. 确认 `person_id` 已解析且状态有效。
2. 确认该平台身份与 `Person` 绑定且未撤销。
3. 确认接收人对该内容具备权限。
4. 确认私聊渠道可用；不可用时降级到其他已验证渠道或等待用户绑定。
5. 写入 `AuditEvent`，记录群聊转私聊的原因和投递结果。

## 4. Audit Log 和 AuditEvent

`Audit Log` 用于解释、回放和追责一次家庭助理行为，不用于替代业务表。业务表记录当前状态，审计表记录不可变事件；如果业务表和审计表出现冲突，排障时以审计链路为事实来源，再通过新的纠正事件修复业务状态。

Audit Log 职责：

- 按 `trace_id` 串起输入、身份、权限、上下文、Codex、工具、确认、通知和输出。
- 按 `operation_id` 解释每个真实世界副作用为什么被允许、拒绝、转确认或降级。
- 保留最终对用户可见的文案引用、投递目标、投递结果和失败降级原因。
- 对高风险动作提供可回放证据：原始输入引用、身份置信度、策略版本、确认记录、HA 状态、幂等记录和执行结果。
- 支持纠正和撤销，但只能追加新事件，不能覆盖历史事件。
- 审计写入失败时阻断真实世界副作用，避免出现“执行了但无法解释”的动作。

`AuditEvent` 是 Audit Log 的最小不可变单元。建议模型：

```python
AuditEvent = {
    "audit_event_id": "string",
    "schema_version": 1,
    "trace_id": "string",
    "span_id": "string",
    "parent_span_id": "string|null",
    "home_id": "string",
    "person_id": "string|null",
    "actor_type": "person|device|system|unknown",
    "module": "relay|identity|policy|context|codex|tool_proxy|ha|task|confirmation|memory|notification|storage|worker",
    "event_type": "string",
    "severity": "debug|info|warning|error|critical",
    "status": "started|succeeded|failed|skipped|rejected",
    "source_trust_level": "trusted_context|user_instruction|weak_user_instruction|untrusted_content|null",
    "risk_level": "low|medium|high|admin|null",
    "sensitivity": "public|household|private|admin_only|secret|null",
    "operation_id": "string|null",
    "idempotency_key": "string|null",
    "policy_version": "string|null",
    "task_id": "string|null",
    "confirmation_id": "string|null",
    "notification_decision_id": "string|null",
    "subject": {},
    "decision": {},
    "input_ref": {},
    "output_ref": {},
    "error": {},
    "redaction": {},
    "retention_until": "datetime|null",
    "created_at": "datetime"
}
```

必记事件：

| 模块 | 事件 |
| --- | --- |
| Relay | 入站消息、来源认证、幂等去重、队列入列 |
| Identity | 候选身份、置信度、绑定、解绑、撤销、合并 |
| Policy | 权限判断、风险判断、拒绝、确认要求 |
| Context | 上下文装配、记忆包含/排除、trust level 保留 |
| Codex Runner | 请求、响应、token、耗时、失败、结构化结果 |
| Tool Safety Proxy | 工具请求、target 展开、HA 状态查询、幂等、执行或确认 |
| HA Adapter | HA MCP 请求、响应、设备离线、状态未知 |
| Confirmation Broker | 创建、投递、批准、拒绝、撤销、过期、状态漂移失效 |
| Task Orchestrator | 创建、锁定、运行、重试、完成、失败、取消、恢复 |
| Memory Pipeline | 候选记忆、确认、写入、纠正、可见性变化 |
| Notification Policy | 投递决策、私聊转发、静默、升级、失败降级 |
| Output Adapter | 渲染、发送、重试、渠道返回、最终可见文案 |
| Storage | 迁移、写失败、读失败、降级到只读 |

审计约束：

- append-only，不能物理修改历史事件；纠正使用新事件表达。
- 敏感素材默认记录引用、摘要、哈希和保留期限，不长期复制音频、视频帧或截图。
- 需要支持按 `trace_id`、`home_id`、`person_id`、`operation_id`、`task_id`、`confirmation_id` 查询。
- 高风险动作必须有完整链路：输入、身份、策略、确认、Tool Safety Proxy、HA、通知。
- 同一个 `operation_id` 的策略判断、确认和工具执行要能排序；推荐使用单调递增 `created_at` 加数据库自增序列或 `audit_event_id` 排序。
- `input_ref` 和 `output_ref` 优先存引用、摘要、哈希和渲染后的安全文本；原始敏感内容放受控存储并设置保留期限。
- 策略、提示词模板、风险规则和通知规则发生变化时，审计事件必须记录对应版本，方便解释历史行为。
- 审计写入失败时，暂停有副作用动作，避免无法追溯的真实世界控制。

## 5. Trace 链路

`trace_id` 贯穿从输入到输出的完整链路。单次用户请求、设备事件或定时触发生成一个根 trace；每个模块创建 span。

推荐链路：

```text
UnifiedMessage
  -> Relay/Event Log
  -> Application Orchestrator
  -> Identity Resolver
  -> Policy Engine
  -> Context Builder
  -> Codex Runner
  -> Tool Safety Proxy / Task Orchestrator / Memory Pipeline
  -> HA Adapter / Confirmation Broker
  -> Notification Policy
  -> Output Adapter
  -> Audit Log / Metrics
```

核心字段传递：

| 字段 | 用途 |
| --- | --- |
| `trace_id` | 一次请求或事件的主链路 |
| `span_id` / `parent_span_id` | 模块内调用层级 |
| `source_event_id` | 关联原始平台事件或设备事件 |
| `message_id` | 去重和回放 |
| `home_id` | 家庭隔离边界 |
| `person_id` | 自然人身份 |
| `operation_id` | 单个有副作用动作 |
| `idempotency_key` | 防止重复执行 |
| `task_id` / `run_id` | 任务和运行 attempt |
| `confirmation_id` | 确认请求 |
| `audit_event_id` | 审计事件 |

Trace 要求：

- 一个 trace 可以包含多个 `operation_id`，例如“关灯并设置空调”。
- 有副作用动作不能只依赖 `trace_id` 幂等，必须生成独立 `operation_id` 和 `idempotency_key`。
- 任务恢复、确认通过后的继续执行可以沿用原 `source_trace_id`，同时创建新的恢复 trace。
- 48 小时会话压缩、重试和补偿通知都要写入 trace，避免背景任务变成黑盒。
- 对用户展示的错误信息可以简化，但内部 trace 要保留足够排错信息。

## 6. 指标和测试集

可观测性需要同时覆盖运行质量、安全边界和用户体验。

运行指标：

| 指标 | 说明 |
| --- | --- |
| `message_ingest_total` | 入站消息数量，按来源、home、类型分组 |
| `message_dedup_total` | 被去重的重复事件 |
| `trace_latency_ms` | 端到端耗时 |
| `codex_latency_ms` / `codex_token_total` | Codex 耗时和 token 使用量 |
| `codex_error_total` | Codex 调用失败 |
| `tool_invocation_total` | 工具调用数量，按工具、风险、状态分组 |
| `tool_success_rate` | 工具执行成功率 |
| `ha_offline_total` | HA 或设备离线次数 |
| `idempotency_hit_total` | 幂等命中次数 |
| `confirmation_created_total` | 确认请求数量 |
| `confirmation_expired_total` | 确认过期数量 |
| `notification_delivery_total` | 通知投递结果，按渠道和状态分组 |
| `notification_delivery_latency_ms` | 从决策到投递成功或失败的耗时 |
| `private_redirect_total` | 群聊转私聊次数 |
| `notification_policy_decision_total` | 通知策略决策数量，按 decision 和 reason_code 分组 |
| `audit_write_failure_total` | 审计写失败，需告警 |
| `audit_event_total` | 审计事件数量，按 module、event_type、severity 分组 |
| `degraded_mode_total` | 进入降级模式次数，按故障类型分组 |
| `task_recovery_total` | 服务重启后的任务恢复数量 |
| `memory_correction_total` | 用户纠正或删除记忆次数 |

安全指标：

- 高风险动作被拒绝、转确认、执行成功的比例。
- 身份不确定导致的澄清次数。
- untrusted content 触发工具请求但被拦截的次数。
- 群聊敏感内容私聊转发次数。
- 审计缺失、策略缺失、HA 状态未知导致的拒绝次数。
- 高风险确认被批准、拒绝、过期、撤销的分布。
- 离线降级期间被拒绝或延迟的真实世界副作用数量。

最小告警规则：

- `audit_write_failure_total > 0` 立即告警并进入只读/无副作用模式。
- HA 或数据库不可用超过阈值时通知 owner，且通知事件本身写入审计。
- 高风险动作在缺少确认、缺少 HA 状态或缺少审计时出现执行记录，视为 critical。
- 群聊敏感内容投递命中失败用例时触发安全回归阻断。

测试集：

| 测试集 | 覆盖目标 |
| --- | --- |
| 身份识别测试集 | 平台身份、语音置信度、群聊 speaker、未知用户 |
| IoT 控制意图测试集 | 低风险开灯、相对亮度、区域展开、批量目标 |
| 高风险动作拒绝测试集 | 门锁、燃气、摄像头隐私、大功率设备 |
| Prompt injection 测试集 | Camera OCR、HA 设备名、网页、群聊陌生人、ASR 误触发 |
| 多用户冲突测试集 | 父母权限、小孩权限、访客授权、多人确认 |
| 通知策略测试集 | 原路回复、群聊转私聊、静默、升级、勿扰 |
| 审计完整性测试集 | 输入到输出的 trace 是否完整，失败分支是否留痕 |
| 48 小时压缩恢复测试集 | session maintenance、摘要、恢复后上下文 |
| 任务恢复测试集 | worker 崩溃、lease 过期、重试、重复触发 |
| 离线降级测试集 | Codex、消息平台、HA、数据库、Camera 不可用 |

MVP 阶段应把真实审计日志抽样转成回归样例，但要先做脱敏。每条回归样例至少包含：输入摘要、身份上下文、风险等级、期望策略决策、期望通知目标、期望审计事件序列和禁止出现的泄露字段。

## 7. 离线、降级和恢复

家庭系统不能假设云服务、Codex、消息平台、HA 或数据库永远可用。降级策略要优先保护家庭安全和审计完整性。

降级矩阵：

| 故障 | 允许能力 | 禁止或降级 |
| --- | --- | --- |
| Codex 不可用 | 本地预设自动化、状态查询、安全告警、固定模板回复 | 复杂推理、新自动化生成、长期记忆推断 |
| 消息平台不可用 | 切换其他已验证渠道、本地语音、延迟投递 | 不向未验证渠道发送敏感内容 |
| HA MCP 或 IoT 网关不可用 | 返回不可用状态、标记设备离线、告警 owner | 停止执行控制，不编造状态 |
| 数据库不可用 | 内存级安全告警、只读健康检查 | 暂停有副作用动作、暂停确认创建、暂停记忆写入 |
| 审计写入失败 | 健康告警、只读查询 | 停止真实世界副作用 |
| Camera 不可用 | 返回不可用状态、保留查询请求 | 不编造识别结果，不用旧截图冒充实时结果 |
| 私聊渠道不可用 | 尝试其他已验证私聊渠道、提示用户绑定 | 不在群聊泄露敏感内容 |
| Task worker 重启 | 根据任务表恢复 pending/running/expired 任务 | 不重复执行缺少幂等 key 的动作 |

恢复策略：

- 启动恢复顺序先检查数据库和审计写入能力，再恢复任务、确认和投递队列；审计不可写时不恢复任何有副作用执行。
- 服务重启后扫描 `Task` 和 `TaskRun`，恢复未完成、未过期且 lease 已过期的任务。
- 确认请求恢复后重新投递或标记过期，不能静默丢失。
- 任务触发后仍重新进入 `Policy Engine` 和 `Tool Safety Proxy`，不复用旧授权直接执行。
- HA 恢复后只恢复可证明幂等的动作；非幂等动作需要重新确认。
- 消息平台恢复后合并重复通知，避免多平台风暴。
- 数据库恢复后补写可安全补写的运行指标，审计事件不能凭空伪造。
- 降级期间积压的通知恢复投递前必须重新跑 `Notification Policy`，避免家庭模式、成员权限或私聊可用性已经变化。

高风险默认规则：

- 身份不确定时拒绝或澄清。
- 系统不完整时拒绝或确认。
- 缺少审计时拒绝。
- HA 状态未知时拒绝。
- 私聊不可达时不在群聊泄露。

## 8. 数据存储

第一阶段优先 SQLite，长期服务可迁移 PostgreSQL。队列优先使用数据库任务表，后续再引入 Redis Streams。

核心表：

| 表 | 内容 |
| --- | --- |
| `messages` | 标准化输入、原始引用、去重 key、trace |
| `audit_events` | append-only 审计事件 |
| `notification_decisions` | 通知策略决策和原因码 |
| `delivery_attempts` | 每次渠道投递、状态、错误和重试 |
| `notification_policy_versions` | 通知策略版本、启用时间和变更说明 |
| `tasks` | 持久任务 |
| `task_runs` | 每次执行 attempt |
| `confirmation_requests` | 确认请求 |
| `confirmation_decisions` | 用户批准、拒绝、撤销记录 |
| `tool_invocations` | 工具请求、策略结果、HA 调用引用 |
| `idempotency_records` | 幂等 key、执行结果、过期时间 |
| `memory_candidates` | 候选记忆 |
| `memory_entries` | 已批准记忆 |
| `context_assembly_records` | 本次上下文装配清单 |
| `metrics_rollups` | 可选的小时级或天级指标聚合 |

文件存储：

- `data/workspaces/users/{home_id}/{person_id}/` 保存用户隔离的 Codex 工作目录。
- `data/workspaces/home/{home_id}/` 保存家庭级任务产物。
- Camera 截图和视频片段放本地 NAS 或 `data/media/`，数据库只存引用、哈希、保留期限和访问控制。
- 审计日志不要无限期复制大体积素材。

存储约束：

- `home_id` 是所有核心表的隔离字段。
- `audit_events` 至少索引 `trace_id`、`operation_id`、`task_id`、`confirmation_id`、`created_at`。
- `delivery_attempts` 使用 `output_id + channel + recipient + attempt_no` 约束重复投递记录。
- `idempotency_records` 使用 `home_id + idempotency_key` 唯一约束，并记录过期时间和最终结果引用。
- 私密内容需要字段级敏感标记，日志和指标默认脱敏。
- append-only 表只新增，不原地修改历史语义。
- 所有外部平台 id 和 token 按最小可见原则存储。
- 迁移脚本必须可重复执行，并在审计或运维日志中记录版本。
- SQLite MVP 要显式开启 WAL、外键约束和事务边界；审计事件与对应业务状态变更尽量在同一事务写入，无法同事务时必须写补偿事件。

## 9. Python 工程结构

根 README 建议先初始化 Python 工程：`pyproject.toml`、`requirements.txt`、`src/`、`tests/`。通知、审计和运行保障相关代码建议按以下目录收敛：

```text
src/home_assist_agent/
  orchestrator/
    application.py
  outputs/
    envelopes.py
    renderer.py
    adapters.py
  notifications/
    policy.py
    router.py
    redaction.py
    delivery.py
  audit/
    events.py
    recorder.py
    queries.py
  observability/
    tracing.py
    metrics.py
    evaluation.py
  storage/
    db.py
    migrations/
    repositories/
  tasks/
    orchestrator.py
    worker.py
  confirmations/
    broker.py
  config/
    settings.py
tests/
  test_notification_policy.py
  test_sensitive_private_redirect.py
  test_audit_trace_integrity.py
  test_degraded_modes.py
  test_task_recovery.py
```

模块职责：

| 模块 | 职责 |
| --- | --- |
| `outputs.envelopes` | 定义 `OutputEnvelope`、输出类型和敏感等级 |
| `outputs.renderer` | 把结构化结果渲染为平台消息，不决定投递目标 |
| `notifications.policy` | 计算 `NotificationDecision` |
| `notifications.redaction` | 按敏感等级脱敏、摘要、群聊转私聊 |
| `notifications.router` | 把决策映射到平台 adapter |
| `notifications.delivery` | 投递、重试、失败降级和 `DeliveryAttempt` 记录 |
| `audit.events` | `AuditEvent` 契约和事件类型 |
| `audit.recorder` | append-only 写入、失败处理和高风险阻断 |
| `audit.queries` | trace、operation、task、confirmation 查询 |
| `observability.tracing` | span、trace 上下文传播 |
| `observability.metrics` | 计数器、延迟、错误率和告警指标 |
| `observability.evaluation` | 从审计抽样生成回归测试数据 |
| `storage.repositories` | 消息、通知、审计、任务、确认、工具调用仓储 |

工程约定：

- 业务层依赖抽象接口，不直接依赖具体平台 SDK。
- Codex Runner、Tool Safety Proxy、Task Orchestrator、Confirmation Broker 都通过同一个 trace 上下文写审计。
- 输出 adapter 只负责发送，不做权限判断。
- 通知策略配置应可测试、可版本化，不散落在平台 adapter 里。
- 单元测试覆盖策略分支，E2E 测试覆盖完整 trace。

## 10. MVP 实施顺序

通知、审计和运行保障部分的 MVP 应与整体 MVP 同步落地，不能等设备控制完成后再补。

建议顺序：

1. 定义契约：`OutputEnvelope`、`NotificationDecision`、`AuditEvent`、`DeliveryAttempt`、trace 上下文字段。
2. 建 SQLite 表：`messages`、`audit_events`、`notification_decisions`、`delivery_attempts`、`tasks`、`task_runs`、`confirmation_requests`、`tool_invocations`。
3. 实现 mock output adapter：支持原路回复、私聊、群聊、静默四种结果。
4. 实现 append-only `AuditRecorder`：覆盖入站、身份、策略、Codex mock、工具 mock、通知输出。
5. 实现简化 `Notification Policy`：原路回复、敏感内容私聊、群聊摘要、静默、投递失败降级。
6. 接入 `Tool Safety Proxy` mock：验证低风险控制直接输出、高风险生成确认、拒绝分支完整审计。
7. 接入 `Confirmation Broker`：确认创建、私聊投递、过期、拒绝、批准后重新进入执行链路。
8. 实现基础指标：trace 延迟、Codex mock 耗时、工具成功率、通知投递结果、审计写失败。
9. 实现 Task worker 恢复：一次性任务、确认等待、48 小时 session maintenance 的 trace 和审计。
10. 建 E2E 回归：低风险开灯、高风险门锁拒绝/确认、身份不确定、prompt injection、敏感群聊转私聊、任务恢复、离线降级。
11. 从脱敏审计样本生成第一批固定回归用例，确保策略和审计 schema 调整不会悄悄改变安全边界。

第一阶段完成标准：

- 任意一次 mock 请求可以按 `trace_id` 查到从输入到输出的完整审计链。
- 群聊中的敏感内容不会泄露到群聊。
- 高风险动作不会在无确认、无 HA 状态或无审计时执行。
- 通知失败有可观察的重试、降级或告警。
- 服务重启后未完成确认和任务可恢复。
- 测试集中至少覆盖通知策略、审计完整性、降级策略和 prompt injection。
