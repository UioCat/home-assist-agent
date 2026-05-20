# Home Assist Agent 文档导航

本文档目录承载项目的详细架构与模块设计。根目录 `README.md` 只保留项目总览、第一期范围和文档索引。

本文档不再使用“最小可行产品”作为第一期定义。第一期（本地可用版）不是“最小可跑闭环”，而是“本地单家庭、单 owner 可长期自用的软件版本”。mock 能力用于开发、测试和 showcase，不构成第一期本地自用验收本身。

## 推荐阅读顺序

1. [第一期（本地可用版）](phase-1-local-usable.md)
   先看第一期目标、真实能力、可降档能力、展示增强和验收标准。

2. [本地单用户模式](local-single-user-mode.md)
   理解单 owner 本地自用时哪些安全能力可以降档、哪些不能降。

3. [Showcase 场景](showcase-scenarios.md)
   理解客户展示脚本、demo seed data、决策卡片和 trace replay。

4. [本地运维手册](local-operations-runbook.md)
   理解安装、配置、健康检查、备份恢复、降级和安全急停。

5. [总体架构](architecture.md)
   先看系统分层、架构不变量、核心契约和模块依赖边界。

6. [输入安全](input-security.md)
   理解所有入口如何转为 `UnifiedMessage`，以及 `trust_level`、`ContextBlock`、provenance 如何贯穿链路。

7. [身份与权限](identity-permissions.md)
   理解多用户、跨平台身份、家庭成员关系、角色权限和身份合并/撤销。

8. [工具安全与 HA 边界](tool-safety-ha.md)
   理解 Home Assistant 作为事实源时，Codex 如何通过受控工具安全调用 HA。

9. [任务与确认](tasks-confirmations.md)
   理解提醒、确认、定时触发、会话维护等跨时间任务如何持久化和恢复。

10. [记忆、上下文与会话](memory-context-sessions.md)
   理解 Codex 候选记忆、长期记忆审核、上下文装配和 48 小时会话压缩。

11. [通知、审计与运行保障](notifications-audit-operations.md)
   理解输出投递、敏感内容私聊、审计链路、可观测性、降级恢复和第一期落地顺序。

12. [消息路由与原渠道响应](message-routing.md)
   理解原渠道回复、群聊私聊改道、渠道 fallback、出站调度和投递幂等。

13. [日志、审计与可观测性](logging-observability.md)
   理解 EventLog、AuditEvent、TraceSpan、RuntimeLog 和 Metrics 如何支持按人、按模块、按 trace 排查。

14. [可视化触摸屏与家庭屏幕](visual-surfaces.md)
    理解家庭 Pad、墙面屏、公共屏、私有屏的默认状态、交互边界和安全策略。

## 文档边界

| 文档 | 负责回答的问题 |
| --- | --- |
| [phase-1-local-usable.md](phase-1-local-usable.md) | 第一期到底要做成什么，哪些是真实能力，哪些是展示增强 |
| [local-single-user-mode.md](local-single-user-mode.md) | 本地单用户模式下哪些安全能力可以降档，哪些不能降 |
| [showcase-scenarios.md](showcase-scenarios.md) | 客户展示如何演示、重置和解释系统能力 |
| [local-operations-runbook.md](local-operations-runbook.md) | 本地如何安装、配置、备份、恢复和急停 |
| [architecture.md](architecture.md) | 系统整体怎么分层，哪些边界不能被绕过 |
| [input-security.md](input-security.md) | 输入是否可信，如何防 prompt injection 和跨通道污染 |
| [identity-permissions.md](identity-permissions.md) | 谁在说话，属于哪个家庭，有什么权限 |
| [memory-context-sessions.md](memory-context-sessions.md) | 什么能被记住，如何检索，何时压缩会话 |
| [tasks-confirmations.md](tasks-confirmations.md) | 长任务、确认、超时和恢复如何建模 |
| [tool-safety-ha.md](tool-safety-ha.md) | HA 如何作为事实源，真实设备控制如何安全执行 |
| [notifications-audit-operations.md](notifications-audit-operations.md) | 消息发给谁，如何审计、观测、降级和落地 |
| [message-routing.md](message-routing.md) | 消息从哪个渠道来，如何优先原渠道返回，何时私聊、静默或 fallback |
| [logging-observability.md](logging-observability.md) | 各模块操作如何记录，如何按人/模块/trace 排查，哪些日志可丢或不可丢 |
| [visual-surfaces.md](visual-surfaces.md) | Pad 和触摸屏在什么场景展示什么，如何处理公共屏隐私和本地交互 |

## 维护原则

- 根 README 只放项目总览和索引，不承载详细设计。
- 新增模块设计优先新建或更新 `docs/*.md`。
- 涉及跨模块边界时，先更新 [architecture.md](architecture.md)，再更新对应模块文档。
- 涉及真实世界副作用时，必须同时检查 [tool-safety-ha.md](tool-safety-ha.md)、[tasks-confirmations.md](tasks-confirmations.md) 和 [notifications-audit-operations.md](notifications-audit-operations.md)。
- 涉及用户身份、隐私或记忆时，必须同时检查 [identity-permissions.md](identity-permissions.md)、[input-security.md](input-security.md) 和 [memory-context-sessions.md](memory-context-sessions.md)。
- 涉及渠道回复、投递失败、群聊/私聊切换时，必须同时检查 [message-routing.md](message-routing.md) 和 [notifications-audit-operations.md](notifications-audit-operations.md)。
- 涉及本地 Pad、触摸屏、公共显示或屏幕确认时，必须同时检查 [visual-surfaces.md](visual-surfaces.md)、[identity-permissions.md](identity-permissions.md) 和 [tool-safety-ha.md](tool-safety-ha.md)。
- 涉及排障、审计、指标、调试日志和隐私查看时，必须同时检查 [logging-observability.md](logging-observability.md) 和 [identity-permissions.md](identity-permissions.md)。
