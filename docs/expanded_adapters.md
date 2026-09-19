# 宏观、基金、ETF、Fiona 与港股原文接口

2026-09-18（本机日期）。实现独立放在 `ir_search`，通过既有 Python SDK / MCP 的 `get_data`、`search_materials`、`retrieve` 调用。没有扩大 `deep_research`，没有依赖 Desktop 技能目录。

## 本轮接口

| 用途 | 数据集 / provider | 实现与边界 |
|---|---|---|
| 全球宏观序列 | `macro_series` / `global_macro`，`market="GLOBAL"` | FRED 6 个常用序列；世界银行 5 类指标 × ISO3 国家；ECB 各币种兑欧元日参考汇率。保留原频率、单位、季调、观察期与官方链接。世界银行本机 TLS 连接失败，尚未通过实取。 |
| 中国大陆场外基金 | `fund_profile`、`fund_nav`、`fund_shares`、`fund_holdings`，`market="CN_FUND"` | Wind 主来源；仅 `fund_nav` 已有 JYDB 备用。持仓为已披露股票持仓，不等于全资产组合。 |
| 中国大陆 ETF | `fund_exchange_daily` + `fund_nav` + `fund_shares` + `fund_holdings`，`market="CN_FUND"` | Wind 场内基金行情覆盖 ETF，也包含 LOF、封闭基金；不把全部场内基金标成 ETF。分别提供交易价、净值、份额、持仓。 |
| Fiona 正式数据源 | `futures_daily`、`options_daily`、`derivatives_bars`、`option_risk` / `fiona` | 合约代码、交易所和存续期校验；分页截断、缺合约、模型和单位不确定性显式反馈。 |
| 港股原始披露 | `search_materials(providers=["hkex"])` | 官方证券目录解析代码，再查公告标题索引，按预算读取港交所原始 PDF/HTML。 |
| 公司 IR | `search_materials(providers=["company_ir"])` | 首批种子：腾讯、阿里巴巴、小米；经过核对的官网目录 → PDF 链接 → 正文与引用。未覆盖公司返回缺口，不猜官网。 |

## 配置

本机 `credentials.env` 已增加并启用以下无密钥开关，公开模板中默认关闭：

```dotenv
GLOBAL_MACRO_ENABLED=true
HKEX_ENABLED=true
COMPANY_IR_ENABLED=true
```

基金复用已有 Wind/JYDB 私有配置。Fiona 复用已有 `FIONA_MCP_ENABLED` 和专用 `FIONA_MCP_TOKEN` / `FIONA_MCP`，继续使用固定 Fiona 域名的 Bearer 请求头。没有复制、输出或新增要求提供的密钥。

其他电脑用 `IR_SEARCH_CREDENTIALS_FILE` 指向自己的私有配置。MySQL 驱动是 `[mysql]` 可选依赖，PDF 提取使用 `[extract]`。浏览器、Scrapling 等仍是可选依赖；Fiona 和宏观 HTTP 不新增重型依赖。

## `get_data` 示例

```python
from ir_search import DataRequest, RequestContext, get_data

# 1. 全球宏观：不插值，不改频率，不自动计算同比。
macro = get_data(DataRequest(
    "macro_series", market="GLOBAL", symbols=[
        "FRED.CPIAUCSL", "FRED.DGS10", "ECB.EXR.D.CNY.EUR.SP00.A"],
    start="2025-01-01", end="2025-03-31"),
    context=RequestContext(timeout_seconds=90, max_operations=10))

# 世界银行示例（本机当前连接未通过）：WB.CHN.NY.GDP.MKTP.KD.ZG
# 支持列表见 list_capabilities()["macro_series_catalog"]。

# 2. 场外基金净值：A/C 份额必须分别指定精确代码。
nav = get_data(DataRequest(
    "fund_nav", market="CN_FUND", symbols=["110011.OF"],
    start="2026-09-01", end="2026-09-11"))

# 3. ETF 原始行情。净值和份额分别查询，调用 skill 对齐日期/币种。
etf = get_data(DataRequest(
    "fund_exchange_daily", market="CN_FUND", symbols=["510300.SH", "159915.SZ"],
    start="2026-09-01", end="2026-09-11"))

# 4. Fiona 显式对照来源。仅价格字段可不携带未确认金额/计数。
quote = get_data(DataRequest(
    "futures_daily", provider="fiona", market="CN_FUTURES", symbols=["RB2610.SHF"],
    fields=["close", "settlement"], start="2026-09-11", end="2026-09-11"))

risk = get_data(DataRequest(
    "option_risk", provider="fiona", market="CN_OPTIONS", symbols=["90008034.SZ"],
    start="2026-09-11", end="2026-09-11"))
```

MCP 使用相同数据集和字段。新宏观数据默认 `frequency="native"`；基金默认 `adjustment="none"`，持仓默认 `frequency="report"`，概况默认 `frequency="snapshot"`。`fund_profile` 必须给起止日期，含义是**基金成立日期筛选**，返回当前描述，不是任意历史日的基金概况。

基金按源记录 ID 保留版本，游标签名绑定账户、请求、日期、份额类别等。下一页须用相同参数和 `next_cursor`，并显式设置 `provider=上一页.provenance.provider`，防止分页中切换来源；不保证数据库在跨页期间不发生更新。宏观和 Fiona 不提供伪造的续页游标，截断时返回 `complete=false`，需要缩窄日期/合约范围。

## 必须保留的口径

- 宏观日期是观察期首日，月度/年度不是发布日期。年度日期范围若不包含当年 1 月 1 日，就不会包含该年观测。返回最新修订快照，不支持 `as_of`，不伪装 ALFRED 或历史版本。缺失标记保留 null；不填零、不前向填充。每次最多 5 个序列、每序列最多 5,000 个观测。世界银行与 ECB 的序列身份/币种字段要与请求匹配；FRED 请求头采用标准 Python HTTP 客户端标识以兼容其 CSV 服务。
- 单位净值、累计净值、复权净值是不同字段。净值日期、公告日期分开；JYDB 当前映射缺币种、复权净值和合并标记，返回 null 和 partial。不能靠名称将 A/C、场内与场外份额自动合并。
- 基金份额源表以万份计，本接口转为份。`share_class_shares` 对应 `FUNDSHARE`；`total_shares` 对应 `F_UNIT_TOTAL`，可能涉及分级基金总份额；`combined_shares` 对应 `FUNDSHARE_TOTAL`。保留合并标记、流通份额和变化原因，不把份额变化自动视为净申购或资金流入。
- 基金持仓日期是报告期末，公告日期另外提供。金额为源币种元、数量为股，占净值百分比除以 100 返回 ratio；季度披露可能仅十大持仓，不作全仓重建。没有自动用披露后的持仓回填历史。
- Wind 场内基金成交量由百份转份、成交额由千元转元，同时保留源值。`source_discount_percent` 是供应商的升贴水百分比，未确认其配对净值日期，**不能充当本项目重算的同期溢价率**。IOPV、PCF、实时申赎清单和资金流估算尚未接入。
- Fiona 日线统一金额暂留 null：前次少量样本证明的倍率不能替代供应商单位承诺。成交量/持仓保留供应商数值并标记计数约定未确认；曾观测到的数量冲突仍显示。风险指标均用 `source_*` 命名，模型和价格类型标为未文档化，IV 与 Greeks 不擅自缩放，返回 partial。
- `derivatives_bars` 的日期范围是**交易日**，夜盘可能在前一自然日；不同于 AKShare `futures_intraday` 的自然日边界。保留带 +08:00 的源时间以及源成交量、成交额和持仓。仅限最近 14 个自然日，历史分钟数据源仍留空。期货分钟实取已通过；期权分钟曾超时，扩大单次预算后取得 48 根，仍保留稳定性限制。
- Fiona 仅支持已核对的具体合约表示：四位年月期货/商品期权或沪深八位期权编号，并核对源元数据。郑商所三位月码不自动猜年份；不合并跨来源行或字段。国内 EOD 默认仍为 Wind → JYDB；既有 AKShare 日内路线不变。

## 港股原文与引用

```python
from ir_search import MaterialSearchRequest, MaterialRequest, search_materials, retrieve

found = search_materials(MaterialSearchRequest(
    "Tencent results", symbols=["00700.HK"], providers=["hkex", "company_ir"],
    published_start="2026-05-01", published_end="2026-09-17",
    candidates_per_source=10, text_reads_per_source=2, web_read_mode="auto"))

# 使用 found.items 中的 original_url 调用 retrieve；引用绑定返回正文的字符偏移和哈希。
# bundle = retrieve(MaterialRequest("revenue", [original_url]))
```

港交所股票代码接受 4 或 5 位 `.HK`。当前目录只覆盖活跃证券，不保证已退市公司与历史代码变更；单次时间范围最多 366 天，每公司最多 1,000 条目录记录。`recordCnt`、`hasNextRow` 与候选预算进入诊断，不把“第一批公告”称为全部公告。

公告发布日期使用索引的香港时间；不根据发布日期猜报告期。读取后的原始披露保持 `EXCHANGE_FILING / official_filing`。公司 IR 材料为 `COMPANY / company`，不自动标为经过审计的财报。目录标题与正文分别保留，来源原文是不可信输入文本。

公司 IR 仅跟随经过核对的发行人主机/域名内 PDF 链接，不把第三方 CDN 全域、新闻转载或搜索摘要抬成官方原文。目录不构成全站历史库。无法确认发布日期时显式 unknown；只有原文“即时发布”新闻稿中明确的香港日期行才作为发布日期，其余不从 URL、封面年份或 PDF 创建时间猜测。日期已明确且落在请求窗口外时会过滤。

`list_capabilities()["materials"]["company_ir_catalog"]` 提供当前种子。增加公司时更新包内 CSV、核对官方来源、添加测试；无需修改用户 Desktop。两个新素材来源只处理港股代码，不占用 A 股查询的来源调度预算。

## 本地 skills 与前述项目如何增进本轮

| 参考 | 采用内容 |
|---|---|
| `mutual-fund-data`、`ashare-data` | 单表只读、基金数据字典、份额类别/币种/净值/单位边界；字段以真实数据库结构再次核对。 |
| ETF 溢折价、净值核对 skill | 交易价和净值分离、日期/币种对齐的输入约束；没有迁入未经验证的套利结论或资金流推断。 |
| 宏观周期 skill | 宏观与收益率序列需求；周期识别和投资判断仍留给调用者。 |
| Crawl4AI、Scrapling、Firecrawl | 新 IR 与披露素材复用既有可选读取器。Scrapling 可显式选择 HTML 解析；动态目录按已有规则使用 Crawl4AI；Firecrawl 需明确启用、填 key 并选择模式，本轮没调用收费服务。 |
| TrendRadar | 沿用“发现条目与原文分离、去重、时间和覆盖诊断”的思路；现有 RSS 可接央行发布订阅，RSS 新闻不冒充宏观数值序列。 |
| browser-use/web-ui、MediaCrawler、Agent-Reach | 沿用来源可用性诊断与可选后端边界；本轮官方结构化接口不需要它们的浏览器调度、社媒登录或整套爬虫依赖。 |

本地参考库已补充相关 skill 与官方接口资料，见 `.local/reference_library` 的 48 条索引；这些开发参考不随安装包发布。运行实现未复制上游项目源码。

官方依据：[世界银行 API](https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures)、[ECB SDMX 示例](https://data.ecb.europa.eu/help/api/data-examples)、[FRED CPI 元数据](https://fred.stlouisfed.org/series/CPIAUCSL)、[Fiona MCP](https://www.finoview.com.cn/guide/mcp/)、[港交所标题检索](https://www.hkexnews.hk/search/titlesearch.xhtml)、[腾讯 IR](https://www.tencent.com/investors/results/)、[阿里 IR](https://www.alibabagroup.com/en-US/ir-financial-reports-financial-results)、[小米 IR](https://ir.mi.com/financial-information/annual-interim-reports)。实际结果见 [本轮验收](expanded_adapters_acceptance.md)。
