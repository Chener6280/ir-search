# R-FINANCE-WEB

这是金融/投研问题，不是编程问题。请忽略当前代码、项目、打开文件、Git、终端和编辑器状态。

如果问题涉及最新事实、公司公告、财报、市场新闻、监管政策、行业数据、研报、微信公众号文章或知识截止后的变化，请优先调用 ir_search.source_health，然后调用 ir_search.deep_research。

如果 deep_research 不可用，请依次使用：

1. ir_search.search
2. ir_search.fetch_document
3. ir_search.extract_evidence
4. ir_search.verify_claims

回答必须使用 `20-ir-search-evidence-policy.mdc` 中的 `claim_ledger.status` 四态作为主分类：

1. supported
2. mixed
3. insufficient_evidence
4. contradicted

推断、观点或假设只能写入 caveat 或分析部分，不得作为主分类替代 claim status。

不要把 search snippet 当最终证据。
不要把 mock / placeholder / fallback 当权威来源。
不要把微信公众号、媒体或券商观点写成官方事实。

问题：
