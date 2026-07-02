# R-DEEP-RESEARCH-SMOKE

请测试 ir_search.deep_research 是否可用。

测试问题：
“最近关于 AI 光模块海外需求的公开信息有哪些？”

要求：

1. 先调用 source_health；
2. 再调用 deep_research；
3. 输出 diagnostics；
4. 不要把 search snippet 当最终证据；
5. 使用 claim_ledger.status 四态区分 supported / mixed / insufficient_evidence / contradicted；
6. 如果工具不可用，请按 fallback policy 降级。
