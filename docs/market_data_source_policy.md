# 市场数据来源规则与当前接口

更新：2026-09-15（Asia/Shanghai）。实取与工程验收见[国内数据交付报告](domestic_data_delivery.md)。

## 来源与覆盖

| 数据集 | 首选 / 缺失回退 | 当前支持与限制 |
|---|---|---|
| `securities`（US） | FMP | 指定美股公司的当前名称、场所、币种；不扫描全市场 |
| `prices_daily_basic`（US） | FMP | Light 价格与原始成交计数；复权和窗口完整性未核验，partial |
| `financial_statements_standardized`（US） | FMP | 最近 N 年供应商标准化三表；保留报告币种/财年/披露信息，partial |
| `securities` | Wind / JYDB | A 股证券目录，当前数据库快照 |
| `prices_daily` | Wind / JYDB | 沪深京 A 股未复权 OHLC、股数、成交额；不支持 PIT |
| `financial_statements` | Wind / JYDB | 三表九项核心金额，保留范围、期间、版本和公告日 |
| `futures_daily` | Wind / JYDB | 国内具体期货合约 OHLC、结算、量额、持仓；不拼连续/主力合约 |
| `options_daily` | Wind / JYDB | 具体 ETF、股指和商品期权日线；已知持仓冲突返回 partial |
| `futures_contracts` | Wind / JYDB | 生命周期、乘数、报价单位；完整字段请求当前由 JYDB 提供 |
| `options_contracts` | Wind / JYDB | 类型、行权价、单位、存续期、供应商标的编号；不是调整历史 |
| `trading_calendar` | Wind | 六个国内期货场所的已发布开市日期，不是夜盘时段表 |
| `prices_intraday` | AKShare | 最近 14 自然日内请求，沪深普通 A 股；实际窗口由来源决定 |
| `futures_intraday` | AKShare；Wind 辅助日历 | 具体合约分钟 OHLC、原始量/持仓；自然日期筛选 |
| `options_intraday` | AKShare | 沪深 ETF 期权当日时间点价格、均价、原始成交/持仓；不是 OHLC |
| `derivatives_daily` | 预留 Wind / JYDB | 其他衍生品尚未定义具体品类，不注册假能力 |
| 较长历史日内 | **留空** | 明确返回 `historical_intraday_source_not_configured` |

海外首版 FMP 已完成，见 [FMP 接口指南](fmp_adapter.md)。其他海外能力、基金、宏观及新的网页/公众号/知识星球/IMA 素材接口属于后续阶段。`source_policy` 是选源意图，`list_capabilities.capabilities` 才是实际注册的能力；能力发现不联网，也不证明账户当前可用。

## 连接与单位

本机按用户选择设置 `WIND_MYSQL_TLS_MODE=disabled`，正式 SDK/MCP 使用非 TLS 连接并返回 `non_tls_explicitly_configured`。公开模板默认 `verify_identity`，不会自动降级。JYDB 保持已配置的 TLS。凭证仅从本机私有 env 读取，不访问桌面 skills。

当前账户 Wind A 股成交量乘 100 转股、成交额乘 1000 转元；期货/期权成交额另用 `WIND_MYSQL_DERIVATIVES_AMOUNT_MULTIPLIER=10000`。公开模板留空，不向别的实例盲目复制倍率。衍生品计数不套用 A 股倍率；两家成交额精度差仍保留。

## 回退与质量

- 国内 EOD 默认 registry 按 Wind → JYDB 查询；US 三类已注册数据集只选 FMP。首选未注册、不覆盖所需字段/频率/范围、完整空页、`not_found` 或 `unsupported` 可在已配置的来源顺序中回退。
- 配置、认证、权限、网络、超时、TLS、限流、单位和上游结构错误明确返回，不伪装成覆盖缺失。
- 回退以整次请求为单位，不混合供应商字段或补单个空值。分页/partial 不触发换源；显式 provider 锁定来源。
- 继续分页必须填写上页实际 provider 并保持其他参数。游标绑定账户和请求，包括财务范围、期间及版本。
- complete 表示当前查询页结束且没有已识别的质量失败，不保证历史、股票池和全部交易时段完整。取完全部页面后仍须检查实际覆盖。
- 已知持仓冲突、财务币种未确认、映射不完整等返回 partial；`allow_partial=false` 拒绝交付相应数据行。
- 实际供应商、live、data_vendor、data_table 与诊断全部保留，不冒充交易所原件或经审计报表。

## 财务选择

start/end 筛选**报告期**。默认 `statement="all"`、`statement_scope="consolidated"`、`period_basis="cumulative"`、`revision="original"`。statement 可选 income/balance/cashflow，范围可选 parent；Wind 支持 single_quarter，JYDB 当前明确不支持单季度，不通过累计相减推算。

资产负债表标为 point_in_time。`revision="all"` 只覆盖已映射的 original/adjusted 类型，不意味着全部更正历史。记录 ID、供应商版本码、公告日和计算标识原值保留；不擅自解释供应商计算标志。不适用该表的科目为 null。目前不是全科目、估值或一致预期接口。

Wind 使用报表币种字段。JYDB 所用财务表没有币种字段，默认 currency=null、partial；有适用于该 profile 全部所用表的明确依据后，可设置 `JYDB_MYSQL_FINANCIAL_CURRENCY`，结果注明来自显式配置。不能仅因少量数值对账一致就推定全部报表币种。

```python
from ir_search import DataRequest, get_data

financial = get_data(DataRequest(
    "financial_statements", symbols=["600519.SH"],
    start="2025-12-31", end="2025-12-31",
    statement="income", fields=["total_revenue", "parent_net_profit"],
)).to_dict()

futures = get_data(DataRequest(
    "futures_daily", market="CN_FUTURES", symbols=["RB2610.SHF"],
    start="2026-09-07", end="2026-09-11",
)).to_dict()
```

## 日内边界

安装可选依赖 `.[akshare]`，设置 AKSHARE_ENABLED=true，无需 Key。股票后端由 AKSHARE_STOCK_BACKEND 明确选择，本机为 sina，不自动切换端点。

- Sina 股票只声明 OHLC；请求 `fields=["open","high","low","close"]`。日内成交量汇总与 EOD 存在差异，暂不提供统一量额字段。Eastmoney 提供 OHLC、股数、成交额，但本机实测发生网络失败。
- 股票/期货支持 1m、5m、15m、30m、60m，最多五个标的；14 个自然日是请求限制，不是覆盖保证。少行/空窗不补造，股票零价格转 null，零成交量保留。
- 期货用原生代码 IF2609、RB2610；日线/目录用带交易所后缀的代码。calendar_date 是自然日期；trade_date 由 Wind 已发布日期辅助解析，trade_date_source 与行情来源分开记录。异常午夜或缺少日历时不猜日期。
- 期货保留 source_volume/source_open_interest 和原生报价单位。部分已收盘日的分钟成交量仍与日线不符，不据此计算名义金额或声称全天完整。
- ETF 期权只接八位代码加 .SH/.SZ、当日、1m；字段为 price/average_price，不提供 OHLC。股指/商品期权日内尚未注册；候选 Eastmoney 接口本机探测失败。
- 当前 K 线若标为未来结束时间，一个周期内的行会排除并提示 unclosed_bar_excluded；更远未来时间报错。此规则不证明其他 bar 已固定不变。
- 日内始终 partial，带窗口、延迟、口径和截断诊断；无伪造分页，超限提示缩小查询。
- AKShare 独立进程在超时或取消时终止并回收，不接收数据库 Key 环境变量，也不向 MCP 输出 SDK 日志。

官方接口依据：[股票](https://akshare.akfamily.xyz/data/stock/stock.html)、[期货](https://akshare.akfamily.xyz/data/futures/futures.html)、[期权](https://akshare.akfamily.xyz/data/option/option.html)。

调用方必须读取 status、provenance、complete、next_cursor、diagnostics 和币种/单位，不能只消费 records。

新增基金/ETF 仅净值支持 Wind → JYDB，其余基金数据为已映射的 Wind 单来源；宏观与 Fiona 新数据集的具体路由见 [新增接口](expanded_adapters.md)。Fiona 日线为显式对照来源，没有替换现有国内 EOD 或 AKShare 日内默认路线。
