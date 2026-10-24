# 项目文档索引

当前候选版本：0.2.0rc2。先读 [HANDOFF](../HANDOFF.md)。带日期的验收文档保留当时事实，旧报告中的测试数、默认配置和待办不自动代表当前版本。

## 当前使用与交接

| 文档 | 用途 |
|---|---|
| [综合评审修复](review_resolution.md) / [Windows 复验](windows_review_handoff.md) | 四份评审与 PR #2 的逐项处理、复验边界 |
| [跨电脑部署](standalone_deployment.md) | 新环境安装、凭证、后端、MCP |
| [当前能力](current_capabilities.md) | 来源与操作范围、真实样本、已知未通过项 |
| [来源选择](source_selection.md) | 用户明确选源，缺失选择零调用，迁移说明 |
| [Agent 验收](agent_acceptance.md) | 新三个入口的业务测试，不依赖旧 deep_research |
| [测试报告模板](test_report_template.md) | 跨电脑记录格式 |
| [本轮验证](handoff_validation.md) | 本次代码与打包验证结果 |
| [外部参考](external_references.md) / [JSON](external_references.json) | 借鉴项目、技能、服务与安装依赖分开 |
| [项目拓扑](project_topology.md) | 调用与模块关系 |
| [变更记录](../CHANGELOG.md) | 0.2.0rc1 行为变化 |

## 核心接口

- [数据口径](market_data_source_policy.md)、[国内数值交付](domestic_data_delivery.md)、[扩展数据与港股接口](expanded_adapters.md)。
- [素材搜索](material_search.md)、[MCP 工具](mcp_tools.md)、[网页原文读取](web_reader.md)。
- [网页选择与配置](web_material_adapter.md)、[额度回退](web_search_fallback.md)、[RSS / Scrapling / Firecrawl](web_toolkit.md)。
- [来源诊断与续取](platform_reliability.md)、[已审阅恢复规则](reliability_patterns.md)。

## Adapter 文档与验收

入口和最新验收链接集中在[当前能力清单](current_capabilities.md)。该清单中的“样本通过”不代表长期可用或全量覆盖；每次结果里的诊断优先于配置体检。

## 历史资料

- [历史 README](legacy_readme.md)：旧 search / deep_research 操作及阶段性描述。
- [原独立安装记录](history_standalone_deployment.md)：保留早期逐次验收，不作为当前安装步骤。
- [最初改造计划](ir_search_v2_refactor_plan.md)、[最小框架](minimal_framework.md)、[deep_research 取舍](deep_research_review.md)。
- [本地参考库说明](reference_library.md)：私有原文不随测试包分发；公开交接名录已单独提供。
- 原 `tests/acceptance_cases.yaml` 与相关研究工作区模板是兼容流程验收；新功能用本版 Agent 验收文档，勿据旧清单重新扩建 deep_research。
