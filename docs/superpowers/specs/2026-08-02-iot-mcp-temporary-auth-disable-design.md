# IoT MCP 临时关闭鉴权设计

## 目标

暂时关闭 IoT MCP Web 控制台及其 HTTP API 的 Admin Token、Session Cookie 和 CSRF 校验。用户打开控制台后直接进入设备管理页面，无需看到或填写鉴权表单。

本次只关闭入口鉴权，不改变设备控制的风险策略、确认流程、外部调用审计和 `message_id` 链路。

## 方案

在 IoT MCP 配置中增加 `auth_enabled: bool`，对应环境变量 `IOT_MCP_AUTH_ENABLED`，当前默认值为 `false`。

- `auth_enabled=false` 时，HTTP 鉴权依赖直接返回固定的交互式 `owner` 主体。
- 读请求、写请求和需要人工交互的请求使用同一个主体，不再要求 Bearer Token、Cookie 或 CSRF Header。
- 关闭鉴权时即使请求携带无效或过期凭据也忽略这些凭据，不因为残留浏览器状态返回鉴权错误。
- `auth_enabled=true` 时，继续执行现有 Admin Token、签名 Session 和 CSRF 校验，保持原有安全行为和测试。
- 关闭鉴权不会改变默认监听地址 `127.0.0.1`，也不会自动扩大网络暴露范围。

保留现有鉴权实现而不删除，便于后续只通过配置恢复。

## 前端行为

前端启动时仍调用现有 Session bootstrap 接口，以便从后端获知当前模式：

- 关闭鉴权时，bootstrap 返回免鉴权会话信息，应用直接进入控制台，不渲染登录表单。
- 开启鉴权且没有有效 Session 时，继续显示原有 Admin Token 登录页面。
- 关闭鉴权时，侧栏状态显示“本地免鉴权”和“开发模式”，不再宣称 Cookie、Session 或 CSRF 已启用。
- 隔离演示模式 `?demo=1` 保持不变，仍然只使用浏览器内演示数据。

## 后端行为

鉴权关闭时，`authenticated`、`write_principal` 和 `interactive_principal` 三类依赖统一返回 `TrustedPrincipal.web_session("owner")`。这样仍沿用当前“人工交互请求”的风险语义，不把浏览器请求误判为自动化 Admin Token 调用。

`GET /api/v1/auth/session` 返回统一结构：`auth_enabled`、`csrf_token` 和 `expires_at`。关闭鉴权时返回 `auth_enabled=false`，后两个字段为 `null`；开启鉴权且 Session 有效时返回 `auth_enabled=true` 和现有会话数据。前端据此进入控制台并更新状态文案。

`POST /api/v1/auth/session` 在关闭鉴权时不再承担登录职责，直接返回相同的免鉴权响应；开启鉴权时保持现有 Admin Token 换取 Session 的行为。

鉴权开启时，上述端点和依赖保持现有行为。

## 审计与安全边界

- 所有设备写操作继续走现有控制服务、风险策略和审计路径。
- 外部 Provider、HTTP、MCP 或消息通道副作用调用仍必须先成功追加请求审计；审计失败时仍阻断调用。
- 现有 `message_id` 和兼容 `request_id` 必须保持一致并贯穿成功、失败和外部调用分支。
- 请求及响应审计的凭据脱敏规则保持不变；关闭鉴权不允许把历史或未来配置的 Token 写入审计库。
- 高风险自动化操作的确认策略不变。浏览器请求继续按人工交互主体执行现有策略。
- 免鉴权模式仅用于当前本地开发阶段。若将监听地址改为非回环地址，操作者必须先重新启用鉴权。
- 服务在免鉴权模式启动时输出明确警告，但不记录任何凭据值。

## 错误处理

- 鉴权关闭时，不再返回 `unauthorized`、`session_invalid` 或 `csrf_invalid`。
- 业务校验、设备不可用、Provider 失败、审计失败和确认失败继续返回原有稳定错误结构。
- 鉴权开启时，现有鉴权错误码和 HTTP 状态保持不变。

## 测试

后端增加以下覆盖：

1. 默认配置关闭鉴权。
2. 无 Authorization、Cookie 和 CSRF 的读请求成功。
3. 无凭据写请求使用交互式 `owner` 主体并成功进入现有控制流程。
4. 无凭据写请求仍产生完整审计链，审计不可用时仍阻断外部副作用。
5. 显式开启鉴权后，现有 Admin Token、Session 和 CSRF 成功及失败测试继续通过。

前端增加以下覆盖：

1. 后端报告免鉴权模式时直接渲染设备概览，不显示 Admin Token 表单。
2. 免鉴权模式侧栏显示“本地免鉴权 / 开发模式”。
3. 鉴权开启且 Session 无效时仍显示原登录页面。
4. 演示模式行为保持不变。

## 运行方式

默认启动即为免鉴权模式：

```bash
cd modules/iot-mcp/backend
.venv/bin/python -m iot_mcp --mode http
```

需要恢复鉴权时：

```bash
export IOT_MCP_AUTH_ENABLED=true
export IOT_MCP_ADMIN_TOKEN='<由操作者安全注入>'
.venv/bin/python -m iot_mcp --mode http
```

恢复鉴权时仍需通过安全配置渠道注入 Session 签名密钥等凭据，不把凭据提交到仓库。
