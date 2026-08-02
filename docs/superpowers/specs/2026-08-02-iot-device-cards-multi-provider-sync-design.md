# IoT 设备卡片与多 Provider 自动同步设计

日期：2026-08-02
状态：待书面复核

## 1. 背景

当前 IoT MCP 控制台的“设备实例”页面使用表格展示设备，只显示设备名称、区域、信号轨迹、风险和“打开控制台”入口。真实属性和操作能力仅在设备详情页展示，导致用户无法像使用苹果“家庭”App 一样快速识别设备状态并执行常用操作。

后端已经具备通用 `DeviceProvider` 抽象、启动时发现、周期 reconciliation、事件订阅、设备/区域/能力标准化、属性快照和设备控制能力。Home Assistant 是 Provider 之一，不是唯一设备源。

## 2. 已确认目标

- 使用方案 A：按区域分组的极简设备卡片，替换当前表格。
- 每张卡片展示一台标准化 IoT 设备、关键状态、在线性和 Provider 来源。
- 卡片提供设备最主要的快速操作；点击卡片主体进入现有完整设备详情页。
- 所有已配置 Provider 自动同步，Home Assistant 作为其中一个设备源。
- 用户在页面中主动发起的操作属于 `human_interactive`，包括门锁在内的高风险设备都直接执行，不出现二次确认。
- 只有 AI 对模糊指令作自主决策，或 AI 基于事件自主触发高风险操作时，才进入二次确认。
- 保持全链路审计、凭据脱敏和外部副作用前置审计约束。

## 3. 非目标

- 本次不重做设备详情页的完整属性与服务表单。
- 本次不引入新的 Provider 专属前端组件。
- 本次不把 Home Assistant 写死为唯一数据源。
- 本次不新增浏览器端 WebSocket 推送；先复用后端现有 Provider 订阅和本地投影。
- 本次不改变 AI 自主操作的确认策略。

## 4. 方案比较

### 方案 1：前端逐台聚合

前端先调用 `listDevices`，再为每台设备调用详情和实时状态接口。

优点是后端改动少；缺点是产生 N+1 请求、加载状态碎片化、单台 Provider 故障可能拖累整页，也难以保证卡片能力选择规则一致。

### 方案 2：后端设备卡片聚合接口（采用）

后端从设备投影、最新属性快照、物模型和 feature bindings 生成统一的卡片 DTO；前端一次加载并按区域分组。

优点是前端简单、数据语义一致、多 Provider 自然兼容、单设备降级可控，且不会因打开页面额外触发外部 Provider 调用。缺点是需要新增查询 DTO 和聚合测试。

### 方案 3：浏览器实时事件流

后端向浏览器推送 Provider 事件，卡片持续实时更新。

优点是状态最新；缺点是连接管理、断线恢复和前端状态合并复杂。本次保留扩展空间，不立即实现。

## 5. 同步与数据流

采用现有通用 Provider 自动同步链路：

1. `ApplicationContainer.startup()` 对所有已配置 Provider 执行首次发现和同步。
2. reconciliation loop 按 `reconcile_interval_seconds` 周期同步设备、区域、物模型、能力和快照。
3. Provider 事件订阅持续写入属性快照和设备事件。
4. 新增的卡片查询只读取本地标准化投影和最新快照，不在页面 GET 请求中直接调用 HA 或其他 Provider。
5. Provider 同步失败时保留历史投影，将 Provider 标记为 degraded；卡片展示失联或陈旧状态，不伪造在线数据。
6. Provider 页继续保留“手动同步”作为诊断补充，但设备页不依赖手动同步才能工作。

自动同步属于系统触发事件。每次启动同步、周期同步或人工同步都生成一个唯一 `message_id`；该触发下的全部 Provider 子调用沿用同一个 ID，兼容字段 `request_id` 与其相同。Provider 请求、响应和失败必须通过共享 `AuditRecorder` 追加记录并脱敏。若现有自动同步审计未满足该约束，应在实现卡片功能时补齐，不得新增无审计外部调用。

## 6. 后端接口

新增只读接口：

```text
GET /api/v1/device-cards
```

返回扁平卡片列表，由前端按 `area` 分组。建议 DTO：

```json
{
  "device_id": "...",
  "display_name": "客厅落地灯",
  "area": "客厅",
  "provider_id": "home_assistant",
  "provider_type": "home_assistant",
  "device_status": "active",
  "provider_status": "healthy",
  "risk_level": "low",
  "observed_at": "2026-08-02T10:00:00Z",
  "freshness": "fresh",
  "values": {
    "PowerSwitch": true,
    "Brightness": 64
  },
  "primary_control": {
    "kind": "property",
    "identifier": "PowerSwitch",
    "name": "电源",
    "data_type": {"type": "bool", "specs": {}},
    "current_value": true,
    "risk_level": "low"
  },
  "secondary_status": [
    {"identifier": "Brightness", "name": "亮度", "value": 64, "unit": "%"}
  ],
  "capability_count": 3
}
```

聚合规则：

- `values` 来自 `StateRepository.latest_snapshots(device_id)`。
- `primary_control` 只从可写属性中选择简单、一键可逆的能力。
- 优先级为 `PowerSwitch`，其次为 `LockState`；无匹配能力时为 `null`。
- 设备具有多个同类实体时，依据 feature binding 的标准化 capability 与稳定 identifier 选择，不能依赖 Provider 私有字段。
- `secondary_status` 优先选择亮度、当前温度、目标温度、电量等可读能力，最多两个。
- 单台设备缺少模型、binding 或快照时仍返回卡片，并使用空能力或 `unknown` freshness，不让整页失败。

快速操作继续复用现有接口：

```text
POST /api/v1/devices/{device_id}/properties:write
```

不新增绕过 `ControlService` 的快捷控制路径。

## 7. 人工操作安全边界

- 卡片右上角的主控件是明确的人工操作，提交时沿用 `human_interactive` principal。
- 不根据 `risk_level=high` 弹出确认框，不跳转到确认页，不要求再次点击。
- 请求发送后按钮进入 busy 状态，防止重复提交；完成后显示行内结果并刷新卡片状态。
- 失败、unknown 或 accepted 结果必须明确反馈，不把未验证结果显示为成功。
- AI 自主操作仍沿用既有 `autonomous` 路径和高风险确认机制，两条路径必须在策略与测试中分开。

## 8. 页面与交互设计

### Intent

让用户在一屏内理解“家里有哪些设备、在哪里、现在是什么状态、最常用操作是什么”，并能以低认知负担完成快速控制。

### Hierarchy

1. 页面标题与 Provider 同步概况。
2. 搜索、状态过滤和设备数量。
3. 按区域分组的设备卡片。
4. 卡片内的设备图标、名称、关键状态、主操作和来源。
5. 完整详情作为次级路径。

### Palette

延续现有深色系统：石墨黑画布、深蓝灰卡片、低对比结构线。设备启用态使用暖黄，在线状态使用绿色，失联/不可用使用弱化灰，高风险只作为信息性红色标识，不暗示人工操作需要确认。

### Depth 与 Surfaces

采用单一“边框 + 色块”深度策略，不使用渐变和重阴影。卡片默认使用 `panel-primary`，启用态使用暖色混合背景；hover 只轻微提升边框对比度。

### Typography 与 Spacing

沿用 IBM Plex Sans / Mono。设备名称优先，状态次之，Provider 和时间为弱化元数据。桌面卡片最小宽度约 220px，移动端单列，触控目标不小于 44px。

### Signature

区域标题下排列双色状态卡：关闭/静止设备为深蓝灰，开启/活动设备为暖黄色调；卡片右上角使用稳定的圆形主控件。

### 卡片交互

- 点击卡片主体：进入 `/devices/{device_id}` 完整控制页。
- 点击右上角主控件：阻止卡片导航，直接写入目标属性。
- `PowerSwitch`：切换布尔值。
- `LockState`：在 `LOCK` 与 `UNLOCK` 之间切换，人工操作直接执行。
- 亮度、温度和带参数服务继续在详情页操作，避免极简卡片堆叠控件。
- 无简单主操作的设备只展示状态；卡片仍可进入详情。

### 页面状态

- loading：显示卡片骨架或现有 PageState。
- empty：说明尚未同步到设备，并引导检查 Provider。
- partial：失联设备保留卡片，标注“不可用/最后更新”。
- error：聚合接口整体失败时显示 ErrorState 和重试。
- busy：仅锁定被操作卡片的主控件，其他设备保持可用。

## 9. 前端结构

- `DevicesPage` 负责查询、搜索、状态过滤、区域分组和页面状态。
- 新增 `DeviceCard` 负责卡片语义、主操作、忙碌和结果反馈。
- 新增纯函数负责设备状态摘要、启用态和区域排序，便于单元测试。
- 扩展 `IoTApi`、真实客户端和 Demo 客户端的 `listDeviceCards()`。
- 保留现有详情页和 `CapabilityControls`，不复制复杂表单。

区域排序规则：有名称的区域按中文 locale 排序，“未分区”始终最后；区域内设备按 `display_name` 排序。搜索覆盖名称、区域和 Provider。状态筛选至少支持全部、在线、失联。

## 10. 可访问性与响应式

- 区域使用语义化 heading，卡片列表使用 list/article 结构。
- 主控件具有包含设备名和目标动作的 `aria-label`，例如“关闭客厅落地灯”。
- 操作结果使用 `aria-live=polite`。
- 键盘焦点样式沿用全局 focus ring；卡片主体与主控件分别可聚焦。
- 760px 以下使用单列；中等宽度自动变为两列；桌面使用自适应多列。
- 不只依赖颜色表达状态，必须同时展示文字和图标/标记。

## 11. 测试策略

### 后端

- 聚合接口返回多个 Provider 的卡片，不把 HA 写死。
- 正确选择 `PowerSwitch` 和 `LockState` 主操作。
- 快照缺失、模型缺失、Provider degraded 和设备 missing 时仍返回安全 DTO。
- 区域、Provider、freshness、能力数量和 secondary status 映射正确。
- 自动同步启动、周期成功、失败降级与恢复继续通过集成测试。
- 自动同步事件的 `message_id == request_id`，请求/响应/失败均完整审计并脱敏；审计不可用时外部调用按约束阻断。

### 前端

- 按区域分组并把未分区放最后。
- 搜索和状态筛选正确。
- `PowerSwitch` 与 `LockState` 的主控件提交正确目标值。
- 人工高风险卡片操作直接调用 API，页面不存在确认对话框。
- 只禁用正在操作的卡片，并显示成功、失败和 unknown 反馈。
- 卡片主体导航到详情，主控件不会触发导航。
- loading、empty、partial 和 error 状态可见。
- Demo 与真实 API 客户端遵循相同 DTO。

## 12. 验收标准

- 设备实例页不再使用表格，每台设备以区域分组卡片展示。
- 页面数据来自通用 Provider 标准化投影，HA 与其他 Provider 可同时出现。
- 已配置 Provider 在启动、周期 reconciliation 和事件订阅链路中自动更新数据。
- 卡片显示设备关键状态、来源和可用的主要操作。
- 人工点击高风险设备不会出现二次确认；AI 自主高风险路径行为不变。
- 桌面和移动端布局可用，键盘与读屏基本语义完整。
- 后端、前端、构建、lint 和相关审计测试全部通过。
