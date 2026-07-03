# R-LATEST-GUARD

这个问题可能涉及最新事实。请先判断是否需要外部检索。

如果需要外部检索，请优先调用 ir_search.source_health，然后调用 ir_search.deep_research。

如果 ir_search 不可用或结果不足：

- 不要假装知道最新进展；
- 不要编造论文、政策、公告、市场数据、公司事件或发布日期；
- 使用 `40-fallback-policy.mdc` 的 canonical fallback 句：“我无法从当前可用工具中取得足够可核验证据。以下只能作为分析框架，不应视为最新事实判断。”；
- 给出应该核验的关键词、机构网站、公司公告源、数据源或论文库。

若进行了检索或核验，请按 `claim_ledger.status` 四态标注关键结论：supported / mixed / insufficient_evidence / contradicted。推断和假设只能作为 caveat。

问题：
