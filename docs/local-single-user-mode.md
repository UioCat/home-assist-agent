# 本地单用户模式

本文档定义第一期（本地可用版）的安全降档方式。目标是在用户本人本地部署、自用、单家庭的前提下减少平台化复杂度，同时保留真实设备控制必须具备的安全边界。

## 运行假设

- 系统部署在用户本人控制的本地网络或可信机器上。
- 默认只有一个 `home_id` 和一个 owner `person_id`。
- 本地 PWA、local API、CLI 和后台 worker 都属于同一 owner 管理域，但仍要携带 `trace_id`、`home_id`、`person_id`、`source` 和 `trust_level`。
- mock voice、mock camera、mock IM 只用于测试和 showcase，不作为真实授权来源。

## 不可降档边界

以下能力必须保留，即使是本地单用户：

- 所有真实 HA 写操作必须经过 `Tool Safety Proxy`。
- Codex 不直接访问原始 HA 写工具。
- HA 当前状态、服务可用性和实体存在性以实时查询为准。
- 写操作必须匹配 allowlist 和风险策略。
- `toggle`、`increase`、`decrease` 等相对或非幂等动作必须规范化为绝对动作。
- 重复请求、任务重试和平台重放必须命中幂等记录，不能重复制造副作用。
- 高风险真实写操作第一期不执行。
- 审计写失败时不执行真实副作用。
- OCR、网页、设备名、群聊引用、低置信 ASR 等内容不能升级为用户授权。

## 可降档能力

| 能力 | 长期形态 | 第一期开法 |
| --- | --- | --- |
| 身份 | 多平台身份、合并、拆分、撤销 | seed 一个 owner，外部身份用本地 token 或管理脚本绑定 |
| 权限 | RBAC + ABAC + 细粒度 grant | `tool_policies.yml` 或 SQLite 表表达 domain/entity/risk/confirmation |
| 确认 | 多审批人、跨平台、私聊按钮 | 本地确认页，绑定 action hash、过期时间和审计 |
| 通知 | 多平台投递、fallback、渠道能力矩阵 | 本地 PWA 通知、local output、可选 PWA push |
| 任务 | 多 worker、复杂重试、分布式调度 | 单进程 worker + SQLite lease |
| 记忆 | 多用户、共享规则协商、复杂纠错 | 个人明确低风险偏好自动写入；其他手动确认或配置 |
| 可观测 | OTel/Langfuse/Phoenix 导出 | SQLite/JSONL trace replay |
| 屏幕 | 多 Surface、多屏同步、认证 session | 一个本地 PWA/kiosk |

## 默认策略

第一期推荐默认策略：

```yaml
mode: single_user
home_id: local-home
owner_person_id: owner
auth:
  local_token_required: true
  allow_loopback_without_token: false
ha:
  writes_enabled: true
  high_risk_writes_enabled: false
  raw_service_call_enabled: false
audit:
  block_side_effects_when_audit_fails: true
memory:
  auto_approve_private_low_risk_preferences: true
  auto_approve_shared_rules: false
showcase:
  demo_mode_enabled: true
  demo_mode_uses_mock_ha: true
```

## 本地认证

第一期不需要完整用户体系，但需要避免局域网里任何人随便调用 HA 写操作：

- local web UI 使用本地 token 或一次性 setup token。
- local API 要求 header token。
- CLI 可以读取本机配置文件中的 token。
- demo mode 默认使用 mock HA；如果连接真实 HA，也必须遵守真实 allowlist。

## 高风险动作处理

第一期高风险动作完整走策略、确认和审计链路，但终态不是真实执行：

```text
user request
  -> ActionPlan
  -> Tool Safety Proxy
  -> risk=high
  -> ConfirmationRequest or dry-run decision
  -> optional local confirmation
  -> final status: blocked | dry_run | not_supported_in_phase_1
  -> AuditEvent
```

这样既能展示治理能力，也不会在本地自用阶段冒门锁、燃气、摄像头隐私等真实风险。

## 升级路径

从本地单用户模式升级到家庭闭环时，优先补：

1. 第二个真实家庭成员和独立 `person_id`。
2. 一个真实 IM 渠道。
3. 私聊确认和敏感内容改道。
4. 角色模板和少量显式 grant。
5. 管理界面中的身份绑定、解绑和撤销。
