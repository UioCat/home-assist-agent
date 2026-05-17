# 日志、审计与可观测性设计

本文档细化 Home Assist Agent 的日志模块。目标是让每一次输入、身份判断、上下文装配、Codex 调用、工具执行、确认、记忆写入和消息投递都能被排查、回放和治理。

设计参考：

- OpenTelemetry 的 Trace、Log、Metric 三类信号和 GenAI 语义约定。
- Langfuse 中 `trace`、`observation`、`session` 的 LLM 应用可观测模型。
- Phoenix / OpenInference 对 Agent、LLM、工具调用链路的观测思路。

本项目不把这些系统作为强依赖；MVP 先在本地数据库和结构化日志中落地兼容字段，后续可导出到 OpenTelemetry Collector、Langfuse、Phoenix 或其他平台。

## 1. 核心原则

日志模块不是一个单表 `logs`，而是四类信号协同：

| 信号 | 作用 | 是否可丢 | 是否阻断副作用 |
| --- | --- | --- | --- |
| `EventLog` | 入站原始事件事实 | 不可丢 | 是，入口必须先写入 |
| `AuditEvent` | 安全、权限、动作、确认、记忆、投递的不可变审计 | 不可丢 | 是，有副作用动作必须等待审计成功 |
| `TraceSpan` / `SpanEvent` | 跨模块调用树、耗时、错误、token、工具链路 | 可采样 | 否，高风险链路建议全量 |
| `RuntimeLog` | 工程调试日志、异常栈、模块内部诊断 | 可限流/采样 | 否 |
| `MetricsRollup` | 聚合指标和告警输入 | 可重算 | 否 |

审计和调试分离：

- `AuditEvent` 面向解释“为什么发生”，长期保留、强约束、强脱敏、append-only。
- `RuntimeLog` 面向工程排障，短期保留、可采样、不可保存敏感原文。
- `TraceSpan` 面向性能和链路还原，可导出到外部观测系统。
- `MetricsRollup` 面向趋势、告警和容量规划。

## 2. 全链路 Trace

每次请求、设备事件或定时任务生成一个 `trace_id`。推荐 span 树：

```text
message.process
  relay.normalize
  relay.authn
  relay.dedupe
  identity.resolve
  policy.evaluate
  context.build
  codex.run
    codex.tool_call
  tool_proxy.invoke
    ha.query_state
    ha.call_service
    ha.confirm_state
  memory.evaluate_candidate
  task.schedule_or_resume
  confirmation.create_or_decide
  notification.decide
  output.render
  output.deliver
  audit.persist
```

Trace 字段要和 OpenTelemetry 兼容：

```python
TraceSpan = {
    "trace_id": "string",
    "span_id": "string",
    "parent_span_id": "string|null",
    "name": "message.process|codex.run|tool_proxy.invoke",
    "kind": "internal|client|server|producer|consumer",
    "module": "relay|identity|policy|context|codex|tool_proxy|ha|memory|task|confirmation|notification|output",
    "status": "ok|error|unset",
    "start_time": "datetime",
    "end_time": "datetime|null",
    "duration_ms": 0,
    "attributes": {
        "home_id": "string",
        "person_id": "string|null",
        "session_id": "string|null",
        "risk_level": "low|medium|high|admin|null",
        "sensitivity": "public|household|private|admin_only|secret|null",
        "gen_ai.system": "codex",
        "gen_ai.request.model": "string|null",
        "gen_ai.usage.input_tokens": 0,
        "gen_ai.usage.output_tokens": 0,
        "tool.name": "string|null",
        "ha.entity_count": 0
    }
}
```

## 3. RequestTrace

`RequestTrace` 是一次链路的摘要索引，便于快速检索。

```python
RequestTrace = {
    "trace_id": "string",
    "home_id": "string",
    "root_event_id": "string|null",
    "source_channel": "wechat|dingtalk|lark|voice|screen|iot|camera|scheduler|http_mock",
    "actor_person_id": "string|null",
    "session_id": "string|null",
    "task_id": "string|null",
    "status": "started|succeeded|failed|rejected|partial|unknown",
    "risk_level": "low|medium|high|admin|null",
    "sensitivity": "public|household|private|admin_only|secret|null",
    "summary": "string",
    "error_code": "string|null",
    "started_at": "datetime",
    "ended_at": "datetime|null"
}
```

## 4. AuditEvent

`AuditEvent` 是不可变事实单元。它应该记录决策、动作、结果和证据引用，而不是保存大量原始敏感内容。

```python
AuditEvent = {
    "audit_event_id": "string",
    "schema_version": 1,
    "event_seq": 1,
    "trace_id": "string",
    "span_id": "string|null",
    "home_id": "string",
    "actor_person_id": "string|null",
    "actor_type": "person|device|system|unknown",
    "subject_person_ids": ["string"],
    "module": "relay|identity|policy|context|codex|tool_proxy|ha|task|confirmation|memory|notification|output|display|storage|worker",
    "event_type": "string",
    "action_type": "read|write|execute|confirm|notify|remember|forget|merge_identity|system",
    "action_name": "string",
    "resource_type": "message|person|memory|task|confirmation|tool|ha_entity|display_surface|route|system",
    "resource_id": "string|null",
    "severity": "trace|debug|info|warning|error|critical",
    "status": "started|succeeded|failed|skipped|rejected|blocked|unknown",
    "risk_level": "low|medium|high|admin|null",
    "sensitivity": "public|household|private|admin_only|secret|null",
    "source_trust_level": "trusted_context|user_instruction|weak_user_instruction|untrusted_content|null",
    "operation_id": "string|null",
    "task_id": "string|null",
    "confirmation_id": "string|null",
    "notification_decision_id": "string|null",
    "delivery_attempt_id": "string|null",
    "policy_version": "string|null",
    "prompt_version": "string|null",
    "rule_version": "string|null",
    "idempotency_key": "string|null",
    "decision": {},
    "evidence_refs": [],
    "redaction": {},
    "error": {},
    "duration_ms": 0,
    "retention_until": "datetime|null",
    "hash_prev": "string|null",
    "hash_self": "string",
    "created_at": "datetime"
}
```

关键约束：

- append-only，不修改历史事件；纠正用新事件表达。
- 高风险动作必须形成完整链路：输入、身份、策略、确认、Tool Safety Proxy、HA、通知。
- 审计写入失败时阻断真实世界副作用。
- 敏感素材只保存引用、摘要、哈希、保留期，不长期复制音频、视频帧、截图或聊天原文。
- 事件里记录策略版本、提示词版本、规则版本，方便解释历史行为。

## 5. AuditSubject

一个事件可能涉及多个自然人、设备或资源。不要把所有查询压力塞到 `AuditEvent.actor_person_id`。

```python
AuditSubject = {
    "audit_subject_id": "string",
    "audit_event_id": "string",
    "home_id": "string",
    "subject_type": "person|external_identity|ha_entity|room|task|confirmation|memory|display_surface|route",
    "subject_id": "string",
    "relation": "actor|recipient|approver|target|mentioned|owner|viewer|affected",
    "sensitivity": "public|household|private|admin_only|secret",
    "created_at": "datetime"
}
```

这样可以支持：

- 按人看“我相关的所有日志”。
- 按设备看“这盏灯最近被谁操作”。
- 按模块看“Tool Safety Proxy 最近拒绝了什么”。
- 按确认看“某次高风险确认为何失效”。

## 6. RuntimeLog

运行日志面向工程排障，建议使用 JSON Lines 或标准 logging handler 输出，字段贴近 OpenTelemetry Logs Data Model。

```python
RuntimeLog = {
    "timestamp": "datetime",
    "observed_timestamp": "datetime",
    "trace_id": "string|null",
    "span_id": "string|null",
    "severity_text": "INFO|WARN|ERROR|DEBUG",
    "severity_number": 9,
    "module": "string",
    "logger_name": "string",
    "message": "string",
    "attributes": {},
    "error_type": "string|null",
    "error_code": "string|null",
    "debug_ref": "string|null"
}
```

限制：

- 不写原始语音、图片、视频帧、完整聊天原文、平台 token。
- 异常栈可以短期保存，但要脱敏。
- 默认保留 7 到 14 天，可按环境配置。
- 不作为审计事实源。

## 7. 按模块和按人分日志

不要为了“按模块/按人分”复制多份日志。推荐“单一事实写入 + 多维索引 + 可导出视图”。

存储策略：

- `audit_events` 单表或分区表保存不可变事件。
- `audit_subjects` 建人、设备、任务、确认、屏幕等多维索引。
- `request_traces` 保存 trace 摘要。
- `runtime_diagnostics` 保存短期 debug 日志。
- `metrics_rollups` 保存聚合指标。

索引建议：

```sql
CREATE INDEX idx_audit_trace ON audit_events(trace_id, event_seq);
CREATE INDEX idx_audit_home_module_time ON audit_events(home_id, module, created_at);
CREATE INDEX idx_audit_home_actor_time ON audit_events(home_id, actor_person_id, created_at);
CREATE INDEX idx_audit_operation ON audit_events(operation_id);
CREATE INDEX idx_audit_task ON audit_events(task_id);
CREATE INDEX idx_audit_confirmation ON audit_events(confirmation_id);
CREATE INDEX idx_audit_error ON audit_events(home_id, status, severity, created_at);
CREATE INDEX idx_subject_lookup ON audit_subjects(home_id, subject_type, subject_id, created_at);
```

导出视图：

- `logs/by_module/{module}/{date}.jsonl`：给工程排障。
- `logs/by_person/{person_id}/{date}.jsonl`：只导出该人有权查看的摘要。
- `logs/high_risk/{date}.jsonl`：owner/admin 安全复盘。
- `logs/delivery/{channel}/{date}.jsonl`：平台投递排错。

导出视图是派生物，可以重建；`audit_events` 才是事实源。

## 8. 隐私、脱敏和访问控制

日志查看本身也要过权限：

```python
LogViewPolicyInput = {
    "viewer_actor_context": {},
    "audit_event": {},
    "requested_detail_level": "summary|detail|raw_ref|debug",
    "reason": "string|null"
}
```

输出：

```python
LogViewDecision = {
    "decision": "allow|redact|deny|require_confirmation",
    "redaction_policy": {},
    "reason_codes": []
}
```

默认可见性：

| 查看者 | 默认可见 |
| --- | --- |
| 本人 | 自己发起、接收、被提及、被影响的摘要和部分详情 |
| 普通家庭成员 | household 低敏事件、公共设备低敏状态、自己相关事件 |
| owner/admin | 全量 trace 摘要和安全审计；raw/debug 需理由和二次确认 |
| 访客 | 仅自己当前 session 的低敏结果 |
| 开发者 | 测试环境 debug；生产数据默认脱敏 |

敏感数据处理：

- Camera：保存事件引用、时间段、对象摘要、哈希，不保存长期截图。
- Voice：保存 ASR 摘要、置信度、音色身份结果，不保存长期原始音频。
- Chat：保存可见文案摘要和平台消息引用，不保存完整敏感原文。
- HA：保存实体引用、状态变化摘要和结果，不长期记录完整状态快照。
- Codex：保存模型、token、工具调用、结构化输出摘要；prompt/context raw 只短期受控保存。

## 9. 必记事件清单

| 模块 | 事件 |
| --- | --- |
| Relay | `message_received`、`source_verified`、`dedupe_hit`、`queued` |
| Identity | `identity_candidates_found`、`identity_resolved`、`identity_merge_requested`、`identity_linked`、`identity_revoked` |
| Policy | `permission_evaluated`、`risk_classified`、`action_allowed`、`action_rejected`、`confirmation_required` |
| Context | `context_assembled`、`memory_included`、`memory_excluded`、`session_summary_used` |
| Codex | `codex_run_started`、`codex_run_completed`、`codex_structured_output_invalid`、`codex_tool_requested` |
| Tool Proxy | `tool_invocation_requested`、`target_expanded`、`idempotency_checked`、`tool_blocked`、`tool_executed` |
| HA | `ha_state_queried`、`ha_service_called`、`ha_state_confirmed`、`ha_result_unknown` |
| Memory | `memory_candidate_created`、`memory_candidate_approved`、`memory_written`、`memory_corrected`、`memory_forgotten` |
| Task | `task_created`、`task_lease_acquired`、`task_run_started`、`task_retry_scheduled`、`task_completed` |
| Confirmation | `confirmation_created`、`confirmation_delivered`、`confirmation_approved`、`confirmation_rejected`、`confirmation_expired`、`confirmation_invalidated` |
| Notification | `notification_decision_made`、`private_redirect_performed`、`delivery_suppressed` |
| Output | `message_rendered`、`delivery_attempt_started`、`delivery_attempt_succeeded`、`delivery_attempt_failed` |
| Display | `screen_session_started`、`screen_policy_decision`、`screen_output_rendered`、`ui_event_received` |

## 10. 告警规则

第一阶段建议内置这些告警：

- `critical`：审计写入失败且有副作用动作等待执行。
- `critical`：高风险动作没有确认却进入 HA 写调用。
- `critical`：敏感内容被投递到群聊或公共屏幕。
- `critical`：不可信输入被写入长期记忆。
- `error`：HA 调用返回 unknown 超过阈值。
- `error`：平台投递失败率连续升高。
- `warning`：身份解析置信度连续偏低。
- `warning`：Codex 结构化输出解析失败率升高。
- `warning`：任务 lease 频繁超时。

## 11. ReplayBundle

排查一次行为时，应能用 `trace_id` 拉出 `ReplayBundle`：

```python
ReplayBundle = {
    "request_trace": {},
    "audit_events": [],
    "trace_spans": [],
    "unified_message_ref": {},
    "context_assembly_record_ref": {},
    "codex_result_ref": {},
    "tool_invocations": [],
    "task_runs": [],
    "confirmation_requests": [],
    "notification_decisions": [],
    "delivery_attempts": []
}
```

ReplayBundle 不一定包含 raw 敏感内容；默认提供摘要和引用，raw 查看另走 `LogViewPolicy`。

## 12. MVP 实现范围

第一阶段实现：

- `request_traces`、`audit_events`、`audit_subjects`、`trace_spans`、`runtime_diagnostics`、`metrics_rollups`。
- 一个统一的 `AuditRecorder`，对高风险/副作用动作同步写审计。
- 一个统一的 `TraceRecorder`，上下文管理器形式包住每个模块调用。
- JSON 结构化日志，携带 `trace_id`、`span_id`、`home_id`、`person_id`、`module`。
- 基础查询：按 trace、person、module、operation、task、confirmation、error。
- 隐私脱敏策略和日志查看权限。
- 本地导出脚本：按模块、按人、按高风险事件导出摘要。

## 13. 参考资料

- [OpenTelemetry Trace API](https://opentelemetry.io/docs/specs/otel/trace/api/)
- [OpenTelemetry Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Langfuse Observability Data Model](https://langfuse.com/docs/observability/data-model)
- [Phoenix](https://arize.com/docs/phoenix)
- [OpenInference](https://arize-ai.github.io/openinference/)
