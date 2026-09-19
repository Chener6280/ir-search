# FMP adapter：美股公司数据首版

FMP 已接入独立安装包、Python SDK 和既有 MCP `get_data` 工具。运行时从私有 env 读取凭证，不依赖桌面 skills、开发机路径或临时验收脚本。适用于用户目前的免费账号；账户升级后沿用同一入口，新增能力仍需开发与验收。

## 当前接口

调用时设置 `market="US"`。默认选源为 FMP，也可显式指定 `provider="fmp"`。仅支持通过当前公司资料核实为 NASDAQ、NYSE 或 AMEX、USD 交易的公司股票；不做全市场扫描，不接 ETF/基金。首轮实取标的为 AAPL，不能据此推定全部股票均有权限。

| 数据集 | 内容 | 数据口径与边界 |
|---|---|---|
| `securities` | 指定股票的名称、交易所、交易币种 | 当前公司快照；不提供历史目录或历史上市状态 |
| `prices_daily_basic` | `price`、`source_volume`、日期、币种 | Light 接口没有 OHLC；默认 `adjustment="source_unspecified"`，不冒充未复权收盘价 |
| `financial_statements_standardized` | 年度 FY 利润表、资产负债表、现金流量表的 12 项核心金额 | 供应商标准化、当前版本快照；默认 `frequency="annual"`，不是原始/重述历史或 PIT |

日线的成交量保留供应商计数，未确认单位与复权规则，不套用国内股数倍率。历史交易币种由当前 profile 提供，并附诊断。单次最多请求 366 个自然日；数据返回不证明每个交易日齐全。

财务金额使用每行 `reportedCurrency`，不会因美股上市就强制转换为 USD。`start/end` 筛选财年结束日，可能不是 12 月 31 日；保留财年、FY、filingDate、acceptedDate。acceptedDate 时区尚未核验，因此以 `accepted_at_source` 原值提供。净利润是供应商 `netIncome`，不改名为国内定义的归母净利润。

`version_id` 是已映射元数据和该表全部已映射金额的内容哈希，与所选字段无关；不是供应商原生记录 ID，也不表示已保存历史版本。`version_basis=provider_current_snapshot`、`statement_basis=provider_standardized_scope_unverified` 明确说明范围。不同报表的期间与披露时点仍需调用方对齐。

日线和财务始终返回 `partial / complete=false`，以反映上述口径或覆盖限制；`allow_partial=false` 会拒绝交付这些行。公司资料在指定股票均返回且未截断时可为 `ok`。金额以十进制字符串跨 JSON/MCP 传输，不丢精度。

## 配置与请求消耗

复制公开 `credentials.env.example` 为私有 `credentials.env`，权限设为 0600。填写 `FMP_API_KEY` 并设置 `FMP_ENABLED=true`。其他电脑通过 `IR_SEARCH_CREDENTIALS_FILE` 指定本机 env 的绝对位置。FMP 使用标准库，无需额外安装金融 SDK。

| 配置 | 默认 | 含义 |
|---|---|---|
| `FMP_MAX_REQUESTS_PER_QUERY` | 5 | 每次查询最多 HTTP 请求数；按最坏情况在联网前检查，允许 1–25 |
| `FMP_ANNUAL_RECORD_LIMIT` | 5 | 每张所选报表请求供应商最近 N 个年度记录，允许 1–100；实际深度依账号权限 |
| `FMP_CACHE_TTL_SECONDS` | 60 | 当前进程内缓存寿命，允许 0–3600，0 关闭 |

冷缓存下，每个标的：公司资料 1 次；基础日线 2 次（含公司资料）；单表财务 2 次；三表财务 4 次。`fields` 中的金额科目决定所需报表；不指定字段或仅请求元数据则读取三表。最多五个股票代码，另受请求预算约束；默认预算下日线最多两个标的、三表最多一个标的。缓存命中可节省实际请求，但不放宽联网前的预算校验。

每个本地 client 的请求开始间隔至少一秒；无自动重试、无自动切换收费端点。缓存最多保存八个响应，按凭证和配置隔离，保留真实抓取时间，不写入磁盘。进程结束缓存失效。这是查询预算和短时节流，不是整个账号跨电脑共享的每日配额管理器。

财务端点读取的是供应商最近 N 条记录，再本地筛选报告期；不能用很早的日期范围强迫服务端返回更久历史。`annual_history_bounded`、`annual_history_may_be_truncated`、`requested_statement_window_empty` 用于说明受限历史或空窗口。当前不提供假分页游标。

## SDK 与 MCP 示例

```python
from ir_search import DataRequest, get_data

company = get_data(DataRequest(
    "securities", market="US", symbols=["AAPL"],
)).to_dict()

daily = get_data(DataRequest(
    "prices_daily_basic", market="US", symbols=["AAPL"],
    start="2026-09-07", end="2026-09-14",
)).to_dict()

financial = get_data(DataRequest(
    "financial_statements_standardized", market="US", symbols=["AAPL"],
    start="2024-01-01", end="2026-09-14",
    fields=["revenue", "operating_income", "net_income"],
)).to_dict()
```

MCP 调用同一个 `get_data`，示例参数：

```json
{
  "dataset": "financial_statements_standardized",
  "market": "US",
  "symbols": ["AAPL"],
  "fields": ["revenue", "net_income"],
  "start": "2024-01-01",
  "end": "2026-09-14"
}
```

先用 `list_capabilities` 和 `describe_dataset` 发现字段、单位与范围；它们只读配置，不联网，不证明账户当前权限。国内 `financial_statements` 的 statement/scope/period_basis/revision 选择器不适用于 FMP；FMP 通过字段选择报表，不宣称国内默认 original/consolidated 语义。

所有调用方都必须读取 `status`、`complete`、`provenance` 和 `diagnostics`。来源为 `fmp / live / data_vendor / data_table`，`source_tier=null`：现有层级没有数据供应商档位，不借用监管/交易所层级。权限、认证、限流、配额、TLS、网络、超时或结构错误返回明确失败，不当成完整空数据。请求仅经验证证书的 HTTPS 发往 FMP 官方域名，不接受任意地址、不跟随重定向；异常和诊断不包含认证 URL 或密钥。

## Ultimate 升级后的扩展

升级后继续使用本机 env 中的 Key；若供应商更换 Key，仅更新 env。无需修改已有 skills 的统一入口。下一步按官方接口和实际权限分别验收完整 OHLC/复权、季度与更长财务历史、估值和一致预期、更多国家股票及基金/ETF等能力，再逐项注册。当前实现没有预先声明这些能力，也没有因套餐名称自动启用批量下载。

接口依据：[FMP Stable 文档](https://site.financialmodelingprep.com/developer/docs)、[Light 日线](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-light)、[利润表](https://site.financialmodelingprep.com/developer/docs/stable/income-statement)。
