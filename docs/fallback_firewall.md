# Fallback Firewall

`ir_search` 默认不自动 fallback。只有调用方显式设置 `allow_fallback=True` 且 `fallback_policy` 允许时，pipeline 才会尝试 `ir_search/configs/fallback_routes.yaml` 中声明的备用源。

在真正调用备用源之前，pipeline 会读取 `ir_search/configs/source_capabilities.yaml`，按 source authority 和 query intent 做防火墙过滤。

## 核心规则

### FILING 不降级

`Intent.FILING` 查询只能 fallback 到这些 authority：

```text
official_filing, regulator, company
```

因此公告、交易所披露、监管文件查询不会降级到：

```text
searxng, web_search, commercial_search, media, ugc
```

### 结构化数据只在数据源之间流转

当查询需要硬数据，例如 PE/PB、市值、股东人数、龙虎榜、财务指标、资金流等，fallback 只允许进入：

```text
data_vendor, broker_platform, public_market_data
```

当前数据链路：

```text
tushare -> longbridge -> market_public
longbridge -> market_public
```

### Discovery source 需要显式 fallback

`searxng` 的 authority 是 `discovery`。它不会进入默认 source route，也不能在 `allow_fallback=False` 时被自动触发。

## Diagnostics

被 policy 拦截的 fallback 不会静默消失。`SearchResult.diagnostics` 会保留一条 skipped status：

```json
{
  "source": "searxng",
  "ok": false,
  "adapter_mode": "skipped",
  "coverage_status": "blocked_by_policy",
  "failure_kind": "blocked_by_policy",
  "fallback_parent": "bocha",
  "fallback_hop": 2,
  "skipped": true,
  "skipped_reason": "filing_intent_requires_authoritative_source"
}
```

这让调用方能区分：

- 上游失败；
- 没有凭证；
- 被 fallback firewall 拦截；
- 真正没有结果。

## 配置位置

- Fallback 顺序：[ir_search/configs/fallback_routes.yaml](../ir_search/configs/fallback_routes.yaml)
- Source authority：[ir_search/configs/source_capabilities.yaml](../ir_search/configs/source_capabilities.yaml)
- 过滤逻辑：[ir_search/capabilities.py](../ir_search/capabilities.py)
