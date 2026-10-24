# 变更记录

## 0.2.0rc2 — 2026-10-24，本地候选

- 后续修复：博查迁移当前官方 `api.bocha.cn` 固定端点，c-mac 系统 Python 3.9/LibreSSL 中英文真实搜索通过；TLS/主机名校验和系统网络保持原样。DuckDuckGo 明确验证页改为 `web_content_challenge` 并阻止同一请求再次联网访问该站，保留在线不可用状态；见[补测报告](docs/web_search_transport_repair.md)。
- `search_materials(providers=['web'])` 新增按地区的有序搜索链：国内默认百智/智谱/博查/Exa/Perplexity/DuckDuckGo，海外默认 Exa/Perplexity/Brave/Serper/AnySearch/Tavily/DuckDuckGo。每机私有 env 可改顺序，无 key 跳过；旧 regional/fixed 单引擎保持兼容。
- 每次尝试记录引擎、地区、归一化状态和耗时。成功空结果默认停止；整链失败且曾有 quota 才生成原有 caller_web_search 交接，新增 attempted_providers，保持共享最多 10 条候选预算。
- 新增固定 HTTPS 搜索传输及百智/智谱 MCP 生命周期；禁止重定向和原始供应商错误外泄，无匿名 API 重试或自动付费。智谱日期未知，Exa 类型只接受 auto/lite；Perplexity 仅 Search API。
- 新增 adapter HTTP/路由测试及 wheel 资源验证。c-mac 本轮详情与未通过项见[验收报告](docs/web_search_chain_validation.md)；使用期观察与 b-mac 部署仍未验收，本轮不 merge/push。

## 未发布增强 — 2026-10-03

- 借鉴 Agent Reach，来源诊断新增分操作观察、可选显式查询/网页样本和配置 RSS 快照检查；保持默认零网络、显式选源、预算和稳定失败诊断。
- Scrapling 增加有界表格单元格、跨度与结构指纹；正文外有表格时保留整页文本，避免静默漏数，不解释金融口径。
- 增加可选 Patchright 1.62.1 雪球驱动，保留现有隔离会话与传输策略；默认 Playwright，实验性能力不代表线上取文成功。
- 使用方法与实际验证范围见[增强指南](docs/reach_scrapling_patchright.md)。

## 0.2.0rc1 — 2026-09-18，交接候选

这是 GitHub 源码交接候选的版本标识，尚未声明稳定版或生产上线。

- **行为变化：** `search_materials.providers` 缺失或空列表不再自动选源；返回 `required_inputs`、可选来源与零调用状态。已有用户选择应由调用方继续显式传入。`get_data` 数值路由不变。
- 新核心入口、各独立 adapter、原文和引用、诊断/预算/续取与私有配置均包含在该工作区候选中；能力和已知失败以 [当前清单](docs/current_capabilities.md) 为准。
- 网页支持博查、Exa、兼容 AnySearch；明确额度耗尽时返回 Agent 原生 Web Search 交接，不伪装服务端已完成。
- 整理当前文档入口、跨电脑安装、Agent 验收题库、外部参考名录与交接文件清单。
- `deep_research` 继续兼容维护，暂停扩建；旧 `web_search` 名称含义未改变。

不复制 credentials.env、账号文件、Cookie、会话、私有技能清单或真实素材到发布/交接包。此前各日期的测试记录保留历史效力，不代表 0.2.0rc1 已在所有平台或全部供应商实测通过。


## 2026-10-07: bounded fake-IP DNS compatibility

Public webpage reads can opt into real A-record resolution for exact reviewed hosts through an explicit loopback HTTP proxy. Default behavior is unchanged; private/fake answers, unsafe redirects and stale DNS remain rejected. The original numeric socket pin and TLS hostname verification remain in force. See docs/fake_ip_dns.md.
