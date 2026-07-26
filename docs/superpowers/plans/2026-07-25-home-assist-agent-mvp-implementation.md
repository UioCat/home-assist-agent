# Home Assist Agent MVP 实施计划

规格：[2026-07-25-home-assist-agent-mvp-design.md](../specs/2026-07-25-home-assist-agent-mvp-design.md)

## 实施顺序

1. 建立 Python 和 React 测试基础设施。
2. 以 TDD 实现直接指令解析和危险目标阻断。
3. 以 TDD 实现本地 Codex 命令封装和结构化输出。
4. 以 TDD 实现 Home Assistant Streamable HTTP MCP 客户端。
5. 以 TDD 实现三类指令编排和 FastAPI 契约。
6. 以 TDD 实现 React 指令工作台。
7. 运行全量测试、前端构建、API 冒烟和桌面/手机视觉检查。
8. 更新根 README，给出安装、配置和运行命令。

## 红绿循环

每一项行为遵循：

1. 写一个会因目标行为缺失而失败的测试。
2. 运行该测试，确认失败原因正确。
3. 写最小实现。
4. 运行相关测试和全量测试。
5. 只在绿色状态下整理结构。

## 完成门槛

- Python 测试全部通过。
- React 测试全部通过。
- React 生产构建成功。
- 未配置 HA 时后端和页面正常启动。
- 本地 Codex 可以完成一个 `other` 指令。
- 配置 HA 后健康检查能够列出 MCP 工具。
- 不在没有明确安全测试目标时执行真实 IoT 写操作。
