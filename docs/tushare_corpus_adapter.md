# Tushare 语料 adapter：研报摘要、新闻与政策

2026-09-15：独立 `tushare_corpus` 已接入现有 `search_materials` Python SDK/MCP。三类非空真实样本均通过取材、字段映射、文本提取、关键词匹配与字符引用验收，详见[验收记录](tushare_corpus_acceptance.md)。接口代码和规则位于可安装的 `ir_search` 包内。

## 已接入的内容

| `material_types` | 上游只读工具 | 返回文本层级 | 保留的来源信息 |
|---|---|---|---|
| `research_report` | `research_report` | `abstract`，研报摘要 | 股票、作者、机构、发布日期、报告编号及供应商提供的下载链接 |
| `news` | `major_news` | `extracted_text`，新闻 HTML 提取文本 | 发布媒体、发布时间及原始链接 |
| `policy` | `npr` | `extracted_text`，政策 HTML 提取文本 | 发布机关、发布时间、发文字号及原始链接 |

缺正文或正文读取预算不足时只返回 `metadata`，并附警告；元数据也必须匹配关键词才进入结果。研报不自动下载 PDF；新闻和政策正文来自供应商返回的 HTML，未与原站逐字核验。缺少 URL 时保持空值，不拼接来源链接。

三类来源均标记 `authority=data_vendor`，原发布者另存于 `publisher`。`source_tier` 分别为 broker/media/regulator，`evidence_type` 分别为 broker_report/news/policy_doc；政策的来源层级不等于已核验政府原件。所有返回文本都是不可信来源数据，不作为 agent 指令。

## 私有配置

本机继续使用项目根目录的私有 `credentials.env`，已启用该来源。其他电脑从公开 `credentials.env.example` 复制并填写自己的私有配置，将 `IR_SEARCH_CREDENTIALS_FILE` 指向该文件的绝对路径：

```dotenv
TUSHARE_MCP_TOKEN=
TUSHARE_CORPUS_ENABLED=true
TUSHARE_CORPUS_MAX_CALLS_PER_QUERY=3
TUSHARE_CORPUS_NEWS_SOURCE=新浪财经
```

Token 只填写值，不填认证 URL；不要使用旧数据接口的 `TUSHARE_TOKEN`。POSIX 文件权限须为 0600。公开模板默认关闭，配置状态和 `list_capabilities().materials` 只表示本地注册，不代表已在线验证该电脑的账号。

新闻源可选择：新华网、凤凰财经、同花顺、新浪财经、华尔街见闻、中证网、财新网、第一财经、财联社。本次实际验收仅覆盖新浪财经，其他来源仍需各自验收。单次搜索使用一个配置媒体。

## skills 调用示例

```python
from ir_search import MaterialSearchRequest, RequestContext, search_materials

result = search_materials(
    MaterialSearchRequest(
        question="贵州茅台经营情况",
        symbols=["600519.SH"],
        keywords=["收入", "利润", "动销"],
        published_start="2025-04-01",
        published_end="2025-04-30",
        providers=["tushare_corpus"],
        material_types=["research_report"],
        candidates_per_source=5,
        text_reads_per_source=3,
        max_chars=6000,
    ),
    context=RequestContext(timeout_seconds=45, max_operations=8),
).to_dict()
```

新闻或政策使用相同入口，分别选择 `material_types=["news"]` 或 `["policy"]`。例如查询“智能家居消费政策”，显式设 `keywords=["智能家居"]`，发布日期起止均为 `2026-09-14`，省略股票代码。没有内置词表的自然语言问题建议给出关键词；当前不是远程语义搜索。

MCP 调用现有 `search_materials` 工具，直接传上述请求字段，另可设 `timeout_seconds=45`；不新增 Tushare 专用 MCP 工具。调用方应先阅读 `required_inputs`、`coverage`、`gaps`、`diagnostics`，再使用材料。

## 实际检索范围与预算

当前上游三个 MCP 工具都未声明候选 `limit/offset` 参数。实现采用限定日期取数后在本地按来源返回顺序读取前若干条，再匹配关键词；不能把收到的所有记录视为都已检索。

| 条件 | 实际向上游请求的日期范围 |
|---|---|
| 研报、解析出单个 A 股代码 | 请求日期范围的最后至多 31 个自然日 |
| 研报、未解析出股票代码 | 请求范围的最后一天 |
| 新闻、政策 | 请求范围的最后一天，00:00:00–23:59:59 |

日期范围收窄时返回 `corpus_query_window_restricted`。不自动循环日期、分页或补全长期窗口；对长期研究应由调用方显式拆分日期、控制总调用量，并保留每次范围与缺口。

`coverage[].scans[]` 逐类返回实际 `query_start/end`、`publisher_filter`、股票代码、状态、上游 `received_count` 和本地 `inspected_count`。每次最多 3 个业务工具调用；`candidates_per_source` 在选中的类型之间分配。类型选择顺序为研报、新闻、政策，预算不够时其余类型明确标为 `not_queried_budget`。

`text_reads_per_source` 在新闻、政策间分配，限制实际解析的正文条数。研报摘要作为返回字段读取，不消耗单独的全文读取次数。即使预算只允许解析 3 篇新闻，上游仍可能在一次响应中返回更多 HTML；正文预算不是网络传输条数上限。每条文本另受 `max_chars` 限制，截断标记可见。

上游文档标注研报/新闻/政策单次最多 1000/400/500 条；实测新闻返回 629 条。因此文档上限仅用于覆盖提示，超过时附 `corpus_documented_row_limit_exceeded`；客户端另外强制每次最多 1000 行、16 MiB 响应。超限返回稳定错误，不静默截断协议内容。

所有成功结果保持 `partial`、`complete=false`。空窗口表示本次未收到记录；没有匹配可能源于窗口、候选预算或关键词，不能解释为全库无相关材料。

## 时间、版本与引用

- 研报保留发布日期，`published_at=null`；新闻和政策按上海时区解释供应商时间，并附时区假设和精度未核实警告。供应商时间不保证历史时点可得性。
- 业务期间保持未知，不从发布日期推断“9 月动销”。显式请求业务期间时，匹配结果保留 `business_period_not_verified`。
- 记录包含 `authors`、`source_document_id`、供应商、发布者、原 URL 和抓取时间。内部 `tushare-corpus://...` 是稳定记录标识，当前 `retrieve` 不接受它；有 HTTP(S) 原链接时可另行调用 `retrieve`。
- 引用绑定本次返回文本的 `version_id`、哈希、`source_part` 及字符起止位置；不编造页码。调用方需保存返回结果以供复核，服务目前没有自动持久化材料正文。
- 同 URL 或同类型同文本层级的完整长文本可按公共规则归组；不同 URL、不同文本的同文号政策目前仍可能分成多组。保留文号和 `independence=not_established`，不能按组数认定独立佐证。

## 连接和失败行为

客户端使用标准库实现本次验证过的 MCP Streamable HTTP 子集，支持 JSON/SSE、初始化及会话、长事件和跨块 UTF-8；SDK 不额外依赖完整 MCP 客户端。提供 MCP 服务时沿用项目的可选 `mcp` extra。此客户端不是任意远程 MCP 代理。

只连接固定官方 HTTPS 主机，验证证书与主机名，最低 TLS 1.2；不接受重定向或任意工具名。认证参数在内存组装，错误只暴露稳定代码，不返回原始认证 URL、错误或 Token。每次搜索独立会话，无自动重试、目录扫描或后台同步。一次正常搜索包含初始化、初始化通知及 1–3 次业务调用，即 3–5 次 HTTP 请求；每个 HTTP 请求均计入操作预算，素材服务分派另消耗一次操作。

日期、字段及工具白名单在请求前验证。超时、取消、权限、限流、额度、响应过大、结构错误分别诊断；同一来源中已完成的成功类型可在其他类型出错时保留，整体取消/期限仍由共享请求上下文处理。跨来源失败隔离沿用素材服务。

代码入口：`ir_search/adapters/tushare_corpus.py`、`ir_search/infrastructure/tushare_corpus.py`、`ir_search/material_registry.py`。当前不扩展旧 `deep_research`，不改变 Wind/JYDB/FMP 数值路由。

字段依据：[Tushare 研报](https://tushare.pro/document/2?doc_id=415)、[政策](https://tushare.pro/document/2?doc_id=406)、[新闻](https://tushare.pro/document/2?doc_id=195)及已验证的 MCP schema；协议依据：[MCP Streamable HTTP 2025-03-26](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)。
