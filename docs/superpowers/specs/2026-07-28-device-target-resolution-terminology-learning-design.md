# Home Assist Agent 设备目标解析与个人术语学习设计

日期：2026-07-28

状态：已批准，进入实施

## 1. 背景

当前直接设备控制把 Codex 路由结果中的自然语言目标直接作为 Home
Assistant MCP 工具的 `name` 参数。例如“打开床头灯”最终会调用：

```json
{
  "name": "床头灯"
}
```

当“床头灯”不是 Home Assistant 已登记的名称或别名时，HA 的目标匹配器返回
`MatchFailedError`。当前系统没有在执行前完成以下工作：

- 读取带 `entity_id` 的实时设备事实；
- 合并个人术语和短期术语；
- 生成有限且可审计的目标候选集；
- 限制 Codex 只能选择候选；
- 在执行前重新验证候选仍然有效；
- 在执行成功后学习用户术语。

本设计采用已经确认的推荐方案：

> 确定性候选集 + Codex 语义排序 + 确定性校验 + 执行后静默学习。

## 2. 已确认的产品规则

1. Codex 继续负责理解“床头灯”“里面那个灯”“我睡觉旁边的灯”等自然表达。
2. Codex 只能从系统提供的候选集中选择，不能生成候选集外的
   `entity_id`。
3. 一个术语可以映射单个实体，也可以映射一个稳定实体集合。
4. 术语只记录“称呼到目标”的关系，不记录开关、亮度等动作。
5. 目标解析层不判断风险等级。风险等级由后续独立模块负责。
6. 设备动作先执行。只有整个目标集合执行成功后，才创建术语候选。
7. 个人术语默认记住，不询问“是否记住”。
8. 新术语先进入 `provisional` 状态。
9. 10 分钟内用户没有纠正，术语自动转为个人长期 `approved`。
10. 用户在 10 分钟内表达“不是这个”“不对”“我说的是……”时，驳回或修正
    当前术语。
11. 个人术语不会自动扩散到家庭。只有用户明确说“全家都这么叫”时，才创建
    家庭共享提升请求，并再次确认。
12. 个人术语优先于家庭共享术语，家庭共享术语优先于普通 HA 名称匹配。

## 3. 范围

### 3.1 包含范围

- 为每条设备控制指令提取动作、参数和原始目标表达；
- 从 HA 构建实时实体目录；
- 查询当前用户可见的个人和家庭术语；
- 通过确定性规则生成有限候选；
- 通过 Codex 返回候选选择、置信度、备选项和简短理由；
- 在执行前验证实体身份、家庭归属、目录版本和动作能力；
- 支持单实体和稳定实体集合执行；
- 支持歧义澄清；
- 支持个人 `provisional`、自动确认、纠正和家庭共享提升；
- 为候选、Codex、校验、执行、学习和错误分支增加完整审计。

### 3.2 不包含范围

- 不设计或实现风险等级计算；
- 不扩大当前允许执行的 HA 工具范围；
- 不让 Codex 决定家庭成员权限；
- 不根据统计行为自动生成家庭共享术语；
- 不让 Codex 直接写长期记忆；
- 不实现跨家庭合并、身份迁移或语音身份识别；
- 不把当前状态变化当成新的实体身份版本。

现有 `SafetyPolicy` 暂时继续作为执行器兼容保护，但本功能不会在目标解析层增加
新的风险判断。后续风险模块接收本设计输出的 `VerifiedTarget`。

## 4. 总体架构

```text
交互编排域
  MessageChannel
    -> InstructionRouter
    -> CommandOrchestrator
    -> ClarificationHandler

目标解析域
  TargetExpressionExtractor
    -> HomeAssistantCatalogProvider
    -> TermMemoryStore
    -> CandidateBuilder
    -> CodexGateway.resolve_target
    -> ResolutionVerifier

设备控制域
  VerifiedTarget
    -> RiskPolicyPort（仅保留接口）
    -> DeviceExecutor
    -> HomeAssistantMcpClient

术语学习域
  SuccessfulExecution
    -> TermLearningWriter
    -> ProvisionalFeedbackWindow
    -> TermPromotionWorker
    -> TermMemoryStore

横切能力
  AuditRecorder
```

主控制流是：

```text
用户表达
  -> 路由并提取动作与目标表达
  -> 读取 HA 实时目录
  -> 读取个人和家庭术语
  -> 生成有限候选集
  -> Codex 选择候选
  -> 确定性验证
  -> 设备执行
  -> 成功后创建 provisional 术语
```

## 5. Home Assistant 实体事实源

### 5.1 当前 MCP 能力结论

本地 HA MCP 已确认提供 `GetLiveContext`。它返回设备名称、domain、状态和部分
属性，但不返回稳定 `entity_id`。因此它可以作为对话上下文来源，但不能作为设备
身份和家庭归属的唯一事实源。

当前 MCP 动作工具 `HassTurnOn`、`HassTurnOff` 和 `HassLightSet` 的参数包含
`name`、`area`、`floor`、`domain` 等字段，没有独立的 `entity_id` 字段。

### 5.2 实体目录来源

新增 `HomeAssistantCatalogProvider`，通过 HA 原生只读接口构建实体目录：

- `/api/states`：读取 `entity_id`、实时状态、属性和 `friendly_name`；
- HA WebSocket `config/entity_registry/list`：读取实体别名、设备引用、区域覆盖、
  禁用状态和原始名称；
- HA WebSocket `config/device_registry/list`：读取设备名称、别名和默认区域；
- HA WebSocket `config/area_registry/list`：读取区域名称和区域别名。

新增配置：

```text
HA_BASE_URL=http://homeassistant.local:8123
HOME_ID=local-home
PERSON_ID=local-user
TERM_DB_PATH=data/terms.db
TARGET_RESOLUTION_CONFIDENCE=0.80
TARGET_CANDIDATE_LIMIT=20
TERM_PROVISIONAL_SECONDS=600
```

`HA_BASE_URL` 不从 `HA_MCP_URL` 隐式推导，避免 URL 结构变化造成错误连接。HA
Token 可以复用现有 `HA_TOKEN`，但 Authorization 头和 Token 值不得写入审计。

目录请求、业务响应、HTTP/WebSocket 错误必须通过共享 `AuditRecorder` 记录。

### 5.3 目录快照

```python
HaEntitySnapshot = {
    "home_id": "string",
    "entity_id": "light.bedroom_left",
    "domain": "light",
    "device_id": "string|null",
    "area_id": "string|null",
    "area_name": "string|null",
    "floor_name": "string|null",
    "friendly_name": "string|null",
    "original_name": "string|null",
    "aliases": ["string"],
    "device_name": "string|null",
    "device_aliases": ["string"],
    "state": "string",
    "attributes": {},
    "capabilities": ["turn_on", "turn_off", "set_brightness"],
    "available": True,
}

CatalogSnapshot = {
    "home_id": "string",
    "catalog_version": "sha256",
    "observed_at": "datetime",
    "entities": [HaEntitySnapshot],
}
```

`catalog_version` 只对实体身份、名称、区域和能力等稳定字段做规范化哈希，不包含
`state` 和 `observed_at`。普通状态变化不会造成执行前目录版本持续漂移。

候选生成可以使用最长 5 秒的目录缓存。执行前验证必须读取新的身份目录快照。

## 6. 调用身份

新增内部 `ActorContext`：

```python
ActorContext = {
    "home_id": "local-home",
    "person_id": "local-user",
}
```

当前本地单用户版本由可信 `MessageChannel` 使用配置注入
`HOME_ID` 和 `PERSON_ID`，不接受浏览器直接提交任意 `person_id`。未来消息平台或
登录系统接入时，由已验证的通道适配器构造 `ActorContext`。

每个实体快照都绑定创建该快照的 `home_id`。验证器拒绝任何与当前
`ActorContext.home_id` 不一致的候选。

## 7. 核心数据契约

### 7.1 设备动作意图

```python
DeviceActionIntent = {
    "action": "turn_on|turn_off|set_brightness",
    "target_expression": "床头灯",
    "parameters": {
        "brightness": 40
    },
}
```

直接和间接设备控制都必须提供 `target_expression`。目标表达不能直接进入 MCP
参数。

路由契约同步调整：

- `direct_iot` 返回完整 `DeviceActionIntent`；
- `indirect_iot` 返回 `target_expression` 和 `intent_summary`，后续设备规划只推理
  动作和非目标参数；
- `other` 不返回目标表达；
- 删除执行路径对 `DeviceCommand.target` 的直接使用。

### 7.2 候选目标

```python
TargetCandidate = {
    "candidate_id": "cand_01",
    "target_entity_ids": [
        "light.bedroom_left"
    ],
    "display_name": "卧室左侧台灯",
    "areas": ["卧室"],
    "domains": ["light"],
    "states": ["off"],
    "sources": [
        "personal_provisional|personal_approved|home_shared|ha_alias|ha_name|area|context"
    ],
    "matched_terms": ["床头灯", "卧室左侧台灯"],
    "rule_score": 0.0,
    "evidence": ["string"],
    "catalog_version": "sha256",
}
```

约束：

- `candidate_id` 是当前解析尝试中的不透明编号；
- Codex 输出只允许引用 `candidate_id`；
- `target_entity_ids` 必须排序、去重且非空；
- 单个候选最多包含 20 个实体；
- 候选集合最多包含 20 项；
- 相同实体集合只保留一项，证据合并；
- 普通名称匹配默认生成单实体候选；
- 只有 HA 区域、HA 组或已有术语映射可以生成稳定实体集合；
- 不因同一设备存在多个实体就自动生成实体集合。

### 7.3 Codex 解析结果

```python
TargetResolutionDecision = {
    "status": "selected|ambiguous|no_match",
    "selected_candidate_id": "cand_01|null",
    "confidence": 0.0,
    "alternative_candidate_ids": ["cand_02"],
    "reason": "string",
}
```

Schema 规则：

- `selected` 必须且只能提供一个 `selected_candidate_id`；
- `ambiguous` 和 `no_match` 的 `selected_candidate_id` 必须为空；
- 备选项最多 3 个；
- 所有候选引用必须存在于本次候选集；
- 输出结构中不存在 `entity_id` 字段；
- `confidence` 必须位于 0 到 1；
- `reason` 只作为解释和审计证据，不参与授权或安全判断。

### 7.4 已验证目标

```python
VerifiedTarget = {
    "home_id": "local-home",
    "candidate_id": "cand_01",
    "entity_ids": ["light.bedroom_left"],
    "catalog_version": "sha256",
    "action": "turn_on",
}
```

后续风险模块和执行器只接收 `VerifiedTarget`，不接收原始自然语言目标。

### 7.5 术语映射

```python
TermMapping = {
    "mapping_id": "uuid",
    "home_id": "local-home",
    "scope": "person|home",
    "person_id": "local-user|null",
    "display_term": "床头灯",
    "normalized_term": "床头灯",
    "target_entity_ids": ["light.bedroom_left"],
    "status": "provisional|approved|rejected|superseded",
    "source_message_id": "string",
    "source_candidate_id": "cand_01",
    "catalog_version": "sha256",
    "evidence": {},
    "created_at": "datetime",
    "promote_at": "datetime|null",
    "updated_at": "datetime",
    "supersedes_mapping_id": "uuid|null",
}
```

术语标准化使用 Unicode NFKC、大小写折叠、首尾空白删除、连续空白折叠和外围标点
删除。数据库同时保存用户原始表达，不把标准化文本回显为用户原话。

## 8. 候选生成

`CandidateBuilder` 是纯确定性组件。输入为：

- 当前 `ActorContext`；
- `DeviceActionIntent`；
- `CatalogSnapshot`；
- 当前用户的 `provisional` 和 `approved` 个人术语；
- 当前家庭的 `approved` 共享术语；
- 最近一次澄清上下文。

生成步骤：

1. 先剔除不可用、禁用、跨家庭和不支持当前动作的实体。
2. 精确匹配个人 `provisional` 术语。
3. 精确匹配个人 `approved` 术语。
4. 精确匹配家庭共享术语。
5. 匹配 HA 实体名称、原始名称、实体别名、设备名称和设备别名。
6. 使用区域、楼层、domain、device class、规范化 token 重合度生成补充候选。
7. 使用当前房间和最近澄清选择增加证据，不直接决定最终目标。
8. 合并相同实体集合，计算确定性 `rule_score`，截取前 20 项。

规则分只用于缩小候选集和稳定排序，不直接代替 Codex 的语义选择。

## 9. Codex 目标解析

新增：

```python
CodexGateway.resolve_target(
    *,
    utterance: str,
    action_intent: DeviceActionIntent,
    candidates: list[TargetCandidate],
    message_id: str,
    correlation_id: str | None,
    causation_id: str | None,
) -> TargetResolutionDecision
```

使用固定 `medium` 思考等级。Prompt 包含：

- 用户原始表达；
- 动作和非目标参数；
- 当前候选集及候选证据；
- 明确禁止输出候选外目标；
- 明确歧义时必须返回 `ambiguous`，不能猜测执行。

完整 prompt、命令参数、结构化输出、标准输出、标准错误和失败信息继续由现有
`CodexGateway._run_structured` 审计。

只有以下条件同时满足，才进入验证：

- `status == selected`；
- `confidence >= TARGET_RESOLUTION_CONFIDENCE`，默认 0.80；
- 选择的候选编号存在。

否则返回澄清响应，不执行设备。

## 10. 确定性验证

`ResolutionVerifier` 不信任 Codex 产生的任何实体描述，只使用候选快照中的实体
引用。验证顺序：

1. 选择的 `candidate_id` 存在于本次候选集；
2. 候选 `catalog_version` 与解析时快照一致；
3. 刷新 HA 身份目录；
4. 所有实体仍存在；
5. 所有实体来自当前 `home_id`；
6. 所有实体未禁用且当前可用；
7. 所有实体支持当前动作；
8. 实体集合非空、无重复且不超过 20 项。

如果刷新后目录身份版本发生变化，系统最多重新生成候选并重新调用一次 Codex。
第二次仍发生变化时返回 `target_catalog_changed`，不执行设备。

验证器不判断风险等级，也不根据 Codex 的理由放宽任何规则。

## 11. 歧义与澄清

当以下任一条件出现时，返回 `needs_input`：

- 没有候选；
- Codex 返回 `no_match`；
- Codex 返回 `ambiguous`；
- Codex 置信度低于阈值；
- 候选在验证前失效，但仍有可展示的替代项。

响应最多展示 3 个经验证仍存在的候选：

```python
ClarificationChoice = {
    "choice_id": "choice_1",
    "display_name": "卧室左侧台灯",
    "area_name": "卧室",
    "domain": "light",
}
```

服务端保存一个最长 10 分钟的 `ResolutionAttempt`，绑定：

- 原始 `message_id`；
- 当前 `home_id` 和 `person_id`；
- 候选快照；
- 对用户展示的 `choice_id`；
- 过期时间。

用户下一条消息选择候选时，生成新的 `message_id`，通过
`correlation_id` 关联原始请求，通过 `causation_id` 指向原始请求。不得沿用原
`message_id`。

## 12. 设备执行

### 12.1 单实体

执行器根据动作确定 MCP 工具：

| 动作 | MCP 工具 |
| --- | --- |
| `turn_on` | `HassTurnOn` |
| `turn_off` | `HassTurnOff` |
| `set_brightness` | `HassLightSet` |

当前 MCP Schema 没有单独的 `entity_id` 参数。第一期将经过验证的完整
`entity_id` 放入 `name` 字段，不使用用户原始术语：

```json
{
  "name": "light.bedroom_left"
}
```

间接控制需要 Codex 生成设备计划时，计划 Schema 只能包含工具名和亮度、颜色等
非目标参数，不允许返回 `name`、`area`、`floor`、`domain` 或其他目标字段。
`DeviceExecutor` 删除任何计划中的目标字段，并从 `VerifiedTarget` 注入最终
`name`，防止旧的 `arguments_json` 路径绕过目标验证。

执行前仍使用 MCP 的实时 input schema 做 JSON Schema 校验。

### 12.2 实体集合

实体集合按排序后的 `entity_id` 顺序逐项执行。每个工具调用都必须先写入独立的
`external.request` 审计，再执行 MCP 调用，再写入 `external.response`。

执行采用 fail-fast：

- 已成功的实体记录为 `completed`；
- 第一个失败实体记录为 `failed`；
- 尚未执行的实体记录为 `skipped`；
- 只要存在失败或跳过，整个目标集合不创建术语候选；
- 不自动回滚已经成功的物理设备动作；
- 响应明确报告部分成功，防止用户误以为可以安全重试整个集合。

外部副作用请求审计写入失败时，必须在 MCP 调用前阻断。

## 13. 术语学习生命周期

### 13.1 创建 provisional

只有以下条件全部满足才创建：

- 目标经过 `ResolutionVerifier`；
- 所有目标实体执行成功；
- HA 返回成功结果；
- 原始目标表达非空；
- 术语存储可用。

写入内容包括原始术语、标准化术语、个人身份、家庭、实体集合、候选证据、
`catalog_version` 和来源 `message_id`。不保存动作和动作参数。

术语写入发生在设备执行之后。术语存储失败不能重新执行设备，也不能把已经成功的
设备动作改报为执行失败。响应保持设备执行成功，同时增加
`term_learning_unavailable` 警告并完整审计。

### 13.2 10 分钟自动确认

新增 `TermPromotionWorker`，每 30 秒查询到期的 `provisional` 映射。每次系统
提升使用独立、稳定且幂等的：

```text
message_id = term-promote-<mapping_id>-<created_revision>
request_id = message_id
```

审计链：

```text
system.request
  -> term.promotion_checked
  -> term.approved 或 term.promotion_skipped
  -> system.response
```

`causation_id` 指向创建该术语的用户 `message_id`。应用启动时立即扫描过期但未处理
的候选，保证重启不会永久停留在 `provisional`。

### 13.3 用户纠正

当前用户在 10 分钟窗口内表达明确否定：

- 仅说“不是这个”：把对应 `provisional` 标记为 `rejected`；
- 说“不是这个，是书桌灯”：驳回旧映射，并对新目标表达重新走候选与 Codex
  解析；
- 新目标解析成功后，新映射作为用户明确纠正结果直接进入个人 `approved`；
- 纠正消息不重复执行设备，除非用户同时明确提供了新的设备动作。

所有纠正使用新的用户消息 `message_id`，通过 `correlation_id` 和
`causation_id` 关联原执行。

### 13.4 家庭共享提升

当用户明确说“全家都这么叫”时：

1. 找到当前用户最近的匹配个人术语；
2. 创建 `pending_home_promotion`；
3. 返回一次家庭共享确认；
4. 用户在 10 分钟内明确确认后，写入 `scope=home` 的 `approved` 映射；
5. 超时或否认则取消；
6. 共享映射冲突时必须展示旧目标和新目标，不允许静默覆盖。

个人记忆过程不询问是否记住。只有家庭共享提升需要确认。

## 14. 存储

新增 SQLite 数据库 `data/terms.db`，至少包含：

```text
term_mappings
resolution_attempts
home_promotion_requests
```

`term_mappings` 保存当前映射状态和修订关系。每次状态变化必须：

1. 在事务前准备变更内容；
2. 通过共享 `AuditRecorder` 追加 `term.write.request`；
3. 审计成功后在 SQLite 事务中写入新修订；
4. 写入成功后追加具体结果事件，失败时追加 `term.write.failed`；
5. 不删除旧修订；
6. 使用 `supersedes_mapping_id` 串联历史。

查询只返回每条术语最新、未驳回、未被替代且在当前用户可见范围内的修订。

同名术语写入规则：

- 已存在相同目标的个人 `approved` 映射时，不降级为 `provisional`，只追加使用证据；
- 已存在不同目标的个人 `approved` 映射时，不自动覆盖，也不通过一次成功执行改变
  长期含义；只有用户明确纠正后才能创建替代修订；
- 已存在相同目标的 `provisional` 时，延续原 10 分钟窗口并追加证据；
- 已存在不同目标的 `provisional` 时，保留旧候选并要求用户纠正，不静默替换；
- 个人映射和家庭共享映射冲突时，个人映射优先。

数据库文件权限设置为 `0600`，并使用 WAL 和 busy timeout，与现有存储一致。

## 15. API 响应兼容

扩展 `CommandStatus`：

```text
success|needs_input|blocked|error
```

扩展 `CommandResponse`：

```python
CommandResponse = {
    "message_id": "string",
    "request_id": "与 message_id 相同",
    "status": "success|needs_input|blocked|error",
    "message": "string",
    "resolution": {
        "status": "selected|ambiguous|no_match",
        "confidence": 0.0,
        "choices": [ClarificationChoice],
    } | None,
    "warnings": ["string"],
    "tool_calls": [ToolCallRecord],
}
```

保留现有单个 `tool_call` 字段作为兼容视图：单实体执行时与
`tool_calls[0]` 相同；集合执行时为空，客户端必须查看 `tool_calls`。

## 16. 审计设计

每条用户消息或系统触发必须具有唯一 `message_id`，并保证
`request_id == message_id`。同一链路的所有模块沿用该 ID；跨消息的澄清、纠正和
系统提升使用 `correlation_id`、`causation_id` 关联。

新增事件：

| 阶段 | 事件 |
| --- | --- |
| HA 目录 | `external.request`、`external.response` |
| 候选生成 | `target.candidates_generated` |
| Codex 解析 | 现有 `codex.request`、`codex.response`，purpose=`target_resolution` |
| 确定性验证 | `target.verification_succeeded`、`target.verification_failed` |
| 澄清 | `target.clarification_requested`、`target.clarification_resolved` |
| 设备执行 | 现有 `external.request`、`external.response` |
| 术语学习 | `term.provisional_created`、`term.approved`、`term.rejected`、`term.corrected` |
| 家庭提升 | `term.home_promotion_requested`、`term.home_promotion_confirmed`、`term.home_promotion_cancelled` |

必须完整记录：

- 用户原始输入和最终输出；
- HA 目录业务请求和业务响应；
- 规范化前的候选输入、最终候选快照和规则证据；
- Codex prompt、参数、结构化结果、stdout、stderr 和失败；
- 校验输入、输出和失败原因；
- 每次 MCP 工具业务请求、响应和失败；
- 术语写入、自动提升和纠正。

Authorization、Token、Cookie、密码、API Key 和客户端密钥必须由现有统一脱敏器
在持久化前处理。

## 17. 错误处理

| 场景 | 处理 |
| --- | --- |
| HA 目录不可用 | `error / ha_catalog_unavailable`，不调用 Codex，不执行 |
| 没有候选 | `needs_input / no_match` |
| Codex 输出非法 | `error / invalid_target_resolution_output` |
| Codex 返回歧义或低置信度 | `needs_input / ambiguous` |
| Codex 引用不存在候选 | `error / invalid_target_candidate` |
| 实体已删除 | 重新解析一次，仍失败则 `target_not_found` |
| 目录连续漂移 | `error / target_catalog_changed` |
| 跨家庭实体 | `blocked / target_outside_home` |
| 动作能力不支持 | `needs_input / target_action_unsupported` |
| 执行部分成功 | `error / partial_device_execution`，报告 completed/failed/skipped |
| 执行成功但术语写入失败 | `success` 加 `term_learning_unavailable` 警告 |
| 审计不可用 | 外部副作用前 `error / audit_unavailable`，不得执行 |
| promotion worker 重复触发 | 幂等跳过并审计 `term.promotion_skipped` |

## 18. 测试设计

### 18.1 单元测试

- 术语标准化；
- 个人 provisional、个人 approved、家庭 shared 和 HA 名称的候选优先级；
- 区域、domain、设备类型和 token 重合规则；
- 相同实体集合去重；
- 候选数量和集合大小限制；
- Codex Schema 禁止输出 `entity_id`；
- 低置信度和歧义不执行；
- 跨家庭、已删除、禁用、不可用和能力不匹配被验证器拒绝；
- 目录变化最多重试一次；
- 术语只保存目标，不保存动作；
- 纠正、自动批准、替代和家庭共享冲突。

### 18.2 适配器测试

- HA REST 状态和 WebSocket 注册表正确合并；
- Token、Authorization 和 Cookie 不进入审计；
- 请求和完整业务响应均进入审计；
- HTTP 401、404、超时和断连映射为稳定错误码；
- `catalog_version` 不因普通状态变化改变；
- 实体名称、区域或能力变化会改变版本；
- MCP 使用验证后的实体引用，不使用用户原始术语。

### 18.3 编排测试

- “打开床头灯”唯一候选成功执行；
- 重名“床头灯”返回澄清且零副作用；
- “里面那个灯”结合上下文选择候选；
- Codex 虚构候选 ID 被阻断；
- 执行成功创建 provisional；
- 10 分钟无纠正自动 approved；
- “不是这个”驳回 provisional；
- “不是这个，是书桌灯”创建修正后的 approved；
- 集合执行部分失败不学习术语；
- 术语存储失败不重复执行设备；
- 审计失败时 MCP `call_tool` 调用次数为零。

### 18.4 全链路审计测试

对成功、歧义、目录失败、Codex 失败、校验失败、MCP 失败、部分执行、术语写入失败、
自动提升和用户纠正分别验证：

- `message_id` 在单条链路中完全一致；
- `request_id == message_id`；
- 跨消息通过 correlation/causation 正确关联；
- 事件顺序完整；
- prompt、参数、stdout、stderr、结构化输出和错误完整；
- 外部请求审计先于外部调用；
- 审计事件不可更新和删除；
- 敏感字段全部脱敏。

## 19. 迁移策略

1. 新增数据模型、目录适配器、候选生成器、Codex 解析器和验证器，但默认关闭。
2. 通过 `TARGET_RESOLUTION_ENABLED` 功能开关在测试环境启用。
3. 完成影子模式：生成候选和 Codex 决策，只审计不执行，用于比对旧路径。
4. 影子模式通过后，将直接和间接 IoT 路径统一切换到新目标解析。
5. 删除 `{"name": command.target}` 的直接透传。
6. 开启 provisional 学习和 promotion worker。
7. 功能稳定后删除旧的自然语言目标执行兼容路径。

功能开关关闭时保持当前行为，仅用于迁移。生产切换完成后必须删除旧路径，不能永久
保留可绕过确定性验证的执行入口。

## 20. 验收标准

满足以下条件才视为实现完成：

1. 任何用户自然语言目标都不能未经候选和验证直接进入 HA 执行参数。
2. Codex 输出 Schema 中不存在可自由填写的 `entity_id`。
3. Codex 引用候选外编号时，HA 调用次数为零。
4. 歧义、低置信度、目录不可用和验证失败时，HA 调用次数为零。
5. 唯一高置信度候选使用验证后的实体引用执行成功。
6. 整体执行成功后创建个人 provisional，10 分钟无纠正自动 approved。
7. 用户纠正可以驳回或替代映射，不重复设备动作。
8. 家庭共享只在显式请求和再次确认后产生。
9. 风险等级仍由独立接口负责，目标解析层不包含风险等级规则。
10. 所有成功和失败路径满足 `AGENTS.md` 的全链路审计、脱敏和前置审计要求。
