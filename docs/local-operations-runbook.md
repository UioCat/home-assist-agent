# 本地运维手册

本文档定义第一期（本地可用版）的本地安装、配置、运行、备份和故障恢复要求。它服务于用户本人长期自用，不追求企业级平台运维。

## 本地部署目标

第一期应做到：

- 一条命令启动服务或进入开发模式。
- 一个配置文件完成 Codex、Home Assistant、SQLite、local token 和 demo mode 配置。
- 本地 PWA 可以打开并看到健康状态。
- 服务重启后不会丢失任务、确认、记忆和审计。
- 出故障时用户能看到原因，并能切换只读模式或 demo mode。

## 配置项

推荐配置结构：

```yaml
app:
  mode: local
  base_url: http://localhost:8080
  data_dir: ./data
auth:
  local_token: change-me
codex:
  mode: real
  api_key_env: OPENAI_API_KEY
ha:
  mode: real
  url: http://homeassistant.local:8123
  token_env: HA_TOKEN
  timeout_seconds: 10
storage:
  sqlite_path: ./data/home-assist-agent.sqlite3
  enable_wal: true
safety:
  writes_enabled: true
  read_only_mode: false
  high_risk_writes_enabled: false
showcase:
  enabled: true
  seed_on_start: false
  use_mock_ha: true
```

## 初始化流程

1. 创建数据目录。
2. 初始化 SQLite，开启 WAL 和外键约束。
3. 创建默认 `home_id` 和 owner `person_id`。
4. 写入默认 `ToolPolicy`。
5. 校验 HA URL/token，读取基础实体和区域。
6. 校验 Codex 配置；不可用时进入降级模式。
7. 启动本地 PWA。
8. 写入 `system_started` 审计事件和健康状态。

## 健康检查

本地 UI 至少展示：

- 服务进程是否在线。
- SQLite 是否可写。
- HA 是否可达。
- Codex 是否可用。
- `Tool Safety Proxy` 是否处于写启用、只读或暂停状态。
- 最近一次任务 worker 扫描时间。
- 最近一次审计写入结果。

## 备份

第一期至少支持手动备份：

- SQLite 数据库。
- 配置文件。
- `tool_policies.yml`。
- 本地 PWA/demo seed 数据。
- media 引用清单，不强制复制大体积视频或截图。

备份命令应先暂停 worker 或创建一致性快照，避免任务状态和审计事件不一致。

## 恢复

恢复流程：

1. 停止服务。
2. 替换 SQLite 和配置文件。
3. 启动服务并运行 schema 校验。
4. 检查未完成任务、确认和最近审计事件。
5. 对过期确认和任务做恢复处理。
6. 在 UI 中展示恢复结果。

## 降级模式

| 故障 | 第一期开法 |
| --- | --- |
| HA 不可用 | 禁止真实写操作，允许读取缓存摘要和 demo mode |
| Codex 不可用 | 固定模板回复、状态查询、提醒管理仍可用 |
| 审计不可写 | 阻断真实副作用，允许只读和诊断 |
| SQLite 不可写 | 进入只读诊断页，不创建任务和记忆 |
| PWA 不可用 | local API/CLI 可用于健康检查和安全急停 |

## 安全急停

第一期必须支持急停：

- 暂停所有 HA 写操作。
- 切换只读模式。
- 暂停某个入口。
- 清空 pending 确认或将其全部过期。
- 记录 `safety_pause_enabled` 审计事件。

## 日常维护

- 定期查看失败任务和 HA unknown 结果。
- 定期导出或压缩旧 trace。
- 定期检查 allowlist 是否仍匹配 HA 当前实体。
- 更新 Codex 和 HA token 后写入配置变更审计。
- showcase 前运行 demo reset，避免真实家庭数据混入客户演示。
