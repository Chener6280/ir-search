# SEC 海外原始披露接口

2026-09-17：独立 `sec` adapter 已接入 `search_materials` 和 `retrieve` 的 Python SDK / MCP。无需 FMP Ultimate，也不使用 FMP Key；FMP 继续负责既有数值数据。

## 覆盖与配置

覆盖美国 EDGAR 申报主体，包括在美申报的外国公司，尚不覆盖全部海外交易所。默认查询 `10-K / 10-Q / 8-K / 20-F / 40-F / 6-K` 及各自 `/A` 修订版。`sec_forms` 精确覆盖默认清单；指定 `10-K` 不含 `10-K/A`。其他表单可查询索引，但 XML、纯文本或缺少主文件的申报尚不支持正文。

`symbols` 精确解析 SEC 代码目录，或用 `sec_ciks` 指定发行主体，去重后最多五家公司。不猜测中文公司名，不保证目录包含全部历史证券。

每台电脑在私有 `credentials.env` 配置，不提交个人信息：

```dotenv
SEC_MATERIALS_ENABLED=true
SEC_USER_AGENT="ir-search 你的真实联系邮箱"
SEC_MAX_HISTORY_FILES=2
```

访问者声明必须使用 HTTP 头可接受的 ASCII 字符，仅发送给固定 SEC HTTPS 地址，诊断不回显其值。文件应仅当前用户可读写。跨电脑用 `IR_SEARCH_CREDENTIALS_FILE` 指向当地的配置路径。

[SEC API 说明](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)说明无需 API Key；[访问规范](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)要求声明 User-Agent 并限制总访问速率。本实现同一进程所有 SEC 主机合计至多 4 次/秒，遵守共享超时、取消及操作预算。多进程/多电脑的总速率仍需调用方协调。403、429 或网络故障不触发匿名身份切换、TLS 降级或付费供应商回退。

## 调用

```python
from ir_search import MaterialSearchRequest, MaterialRequest, RequestContext, search_materials, retrieve

result = search_materials(
    MaterialSearchRequest(
        question="Apple 年报", symbols=["AAPL"], providers=["sec"],
        sec_forms=["10-K", "10-K/A"], keywords=["10-K"],
        material_types=["announcement"],
        published_start="2025-09-17", published_end="2026-09-17",
        candidates_per_source=10, text_reads_per_source=2, max_chars=30000,
    ),
    context=RequestContext(timeout_seconds=60, max_operations=30),
)
versions = [v for group in result.items for v in group["versions"]]
if versions:
    original = retrieve(
        MaterialRequest(question="risk factors revenue",
                        urls=[versions[0]["source_ref"]], max_chars=100000),
        context=RequestContext(timeout_seconds=60, max_operations=20),
    )
```

已知 CIK 可用 `sec_ciks=["320193"]` 替代 `symbols`，省去代码目录请求。MCP 使用同名字段；`dry_run=true` 只返回计划和预算。按表单浏览可用表单名作关键词，按主题可给 `keywords=["revenue", "risk"]`，匹配仅覆盖实际读取的标题和正文，并非 SEC 全市场全文检索。调用方必须检查覆盖、诊断、文本层级和截断状态。

## 日期、附件与证据

流程：证券代码/CIK → submissions 索引 → 按申报日、表单过滤 → 按预算读取主文件 → 本地匹配 → 返回原始 URL、元数据、哈希及引用。

- 素材类型为 `announcement`；定期财务报告的证据类型为 `financial_report`，其余为 `announcement`。来源身份为 `official_filing`，等级为 `EXCHANGE_FILING`，发行主体是内容提供者，SEC 托管不等于监管背书。
- `published_on` 使用申报日 `filingDate`。`read_details.filing` 分别保留 `report_date`、原始受理时间和 accession number。不会由报告期末编造完整业务期间，`published_at` 保持未知。原件与修订版按申报号分别保留，即使文本相同也不合并。
- 支持 HTML 与可选 PDF（需项目既有 PDF 依赖）。排除脚本、隐藏区块和 Inline XBRL 隐藏数据，避免隐藏数字混入正文；抽取不保留完整视觉表格，不作为结构化财务数值接口。
- `links` 返回正文实际出现、属于同一申报目录的 HTML/PDF 链接，最多 100 条。将所需附件 URL 再传给 `retrieve`；或显式设置 `follow_links` 与 `link_domains=["www.sec.gov"]` 做有限单层读取。8-K 业绩稿和 40-F 财务报表/MD&A 常在附件中。未读附件不计为证据，也不宣称附件目录完整。
- 直接 `retrieve` 不追加索引请求：CIK、申报号来自 URL，公司名、表单、报告期末若存在则来自 Inline XBRL；申报日保持未知。需要申报日期时保留此前搜索元数据，没有明确标签时不猜测。

## 预算与诊断

每家公司读取当前 submissions 文件，以及至多两个与请求日期重叠的历史文件（可配置 0–2）。索引仅在同一请求上下文内缓存。扫描计数指经过日期和表单过滤的索引记录；候选条数、最新主文件正文读取数分别受请求预算约束。

搜索每篇最多 50,000 字符，`retrieve` 最多 100,000。长文件标记 `text_truncated`，尚无尾部续读；未命中不代表全文不存在该内容。引用位置和 SHA-256 绑定实际返回文本；详情另保留原始响应字节 SHA-1，用于内容一致性识别，不作数字签名证明。

| 诊断 | 含义 |
|---|---|
| `sec_contact_required` | 缺少访问者声明 |
| `sec_symbol_unresolved` | 代码无法唯一解析 |
| `sec_access_blocked` / `rate_limit` | SEC 拒绝程序请求 / 限流 |
| `sec_history_limit_reached` / `sec_candidate_limit_reached` | 历史文件或候选预算限制 |
| `sec_primary_document_unavailable` | 主文件缺失或格式不支持 |
| `sec_primary_text_unavailable` | 正文失败，仅保留索引；具体错误另有诊断 |

保持统一 `partial / unavailable`、`complete=false`，暴露 `source_scan_incomplete`、`bounded_search_not_exhaustive`。`metadata` 不冒充正文。本轮没有全市场全文搜索、自动研判或 legacy `deep_research` 扩建。真实样本和独立安装结果见 [验收记录](sec_filings_acceptance.md)。
