# 变更记录

## 0.2.0rc1 — 2026-09-18，交接候选

这是 GitHub 源码交接候选的版本标识，尚未声明稳定版或生产上线。

- **行为变化：** `search_materials.providers` 缺失或空列表不再自动选源；返回 `required_inputs`、可选来源与零调用状态。已有用户选择应由调用方继续显式传入。`get_data` 数值路由不变。
- 新核心入口、各独立 adapter、原文和引用、诊断/预算/续取与私有配置均包含在该工作区候选中；能力和已知失败以 [当前清单](docs/current_capabilities.md) 为准。
- 网页支持博查、Exa、兼容 AnySearch；明确额度耗尽时返回 Agent 原生 Web Search 交接，不伪装服务端已完成。
- 整理当前文档入口、跨电脑安装、Agent 验收题库、外部参考名录与交接文件清单。
- `deep_research` 继续兼容维护，暂停扩建；旧 `web_search` 名称含义未改变。

不复制 credentials.env、账号文件、Cookie、会话、私有技能清单或真实素材到发布/交接包。此前各日期的测试记录保留历史效力，不代表 0.2.0rc1 已在所有平台或全部供应商实测通过。
