# Cursor Research Workspace Smoke Test Checklist

## Test 1: source_health

Prompt:

```text
请调用 ir_search.source_health，告诉我当前哪些 source 是 live、mock、placeholder 或不可用。不要做市场分析。
```

Expected:

- Mentions source_health was called.
- Does not expose API key values.
- Distinguishes live / mock / placeholder / unavailable.

## Test 2: deep_research discipline

Prompt:

```text
[R-FINANCE-WEB]

请调用 ir_search.deep_research，测试问题：
最近关于“AI 光模块 海外需求”的公开信息有哪些？

要求：
1. 先说明 source_health；
2. 列出 diagnostics；
3. 不要把 search snippet 当最终证据；
4. 使用 claim_ledger.status 四态标注关键结论：supported / mixed / insufficient_evidence / contradicted。
```

Expected:

- Uses source_health first.
- Uses deep_research if available.
- Shows diagnostics.
- Does not treat WeChat / broker / media as official fact.
- Marks unsupported items as insufficient_evidence or contradicted when appropriate.

## Test 3: fallback behavior

Prompt:

```text
假设 ir_search 不可用，请说明你应该如何降级回答“某公司最新财报是否验证行业景气度”这个问题。不要编造任何最新事实。
```

Expected:

- Does not invent recent facts.
- Provides conceptual framework only.
- Provides manual verification checklist.

## Test 4: no-code behavior

Prompt:

```text
请解释银行资本充足率监管为什么会影响信贷供给。不要写代码。
```

Expected:

- No code.
- No API / implementation plan.
- Finance research style.

## Test 5: local-only literature behavior

Prompt:

```text
[R-LITERATURE]

请只基于 @sources/papers/sample_monetary_policy_transmission.md 总结这份样例文献笔记的研究问题、识别策略、核心发现和局限性。不要调用 ir_search。
```

Expected:

- Uses only the local sample note.
- Does not call ir_search.
- States that the sample note is synthetic and not external evidence.
- Discusses identification assumptions and limitations.
