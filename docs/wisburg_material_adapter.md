# 智堡素材 adapter

2026-09-17。来源名 `wisburg`，通过[智堡官方 Agent 接入页](https://www.wisburg.com/#agent)公布的 `https://mcp.wisburg.com/mcp` 使用 TLS + Bearer 鉴权。运行实现全部在 `ir_search`，无需浏览器登录、外部 Skill、Node 或开发者桌面路径。新增基础依赖为零，现有 `deep_research` 不接入此来源。

## 已接入范围

官方工具目录实测版本为 `wisburg-mcp 0.8.4`，返回 13 项工具。本 adapter 接入其中 9 类文本列表和 3 个详情工具。

| `wisburg_categories` | 来源栏目 | 搜索内容类型 | 详情范围 |
|---|---|---|---|
| `ib` | 投行研报 | `research_report` | 智堡笔记摘要 |
| `company` | 公司研究 | `research_report` | 智堡笔记摘要 |
| `am` | 资管报告 | `research_report` | 智堡笔记摘要 |
| `archive` | 央行、政府、智库等公开文献 | `policy` | 智堡笔记摘要 |
| `ec` | 电话会 | `call_transcript` | 智堡笔记摘要 |
| `feed` | 投研资讯流 | `news` | 列表片段或报告笔记摘要 |
| `market_daily` | 市场日报 | `news` | 智堡笔记摘要 |
| `article` | 智堡研究文章 | `opinion` | 智堡原有文章正文，供应商转换的 Markdown |
| `mikko` | Mikko 短篇日志 | `opinion` | 列表自带短篇正文，显式详情可再次读取 |

类型映射是栏目归类，不是逐篇体裁验证。例如 `archive` 中也会包含研究出版物，`ec` 中的文本不保证是逐字电话会记录。结果均带 `source_category_not_verified_document_genre`。

**研报、文献和电话会详情只提供智堡整理的摘要，不提供源文件或下载地址。** 因此 `text_scope=abstract`、`evidence_type=opinion`、`source_tier=MEDIA`，不会因文章讨论央行政策就提升为监管机构原文。出版者记录为智堡，未从正文猜测原始作者。没有原始网址时 `original_url=null`，不会编造链接或页码。

摘要可能经过供应商 AI 辅助整理。摘要文本保守标记 `provenance.generated=true`，并通过 `read_details.generation_basis` 说明这是保守分类，不是逐条识别其作者方式。只读取已存在的摘要，不调用对话、生成或研究编排工具。智堡文章与日志的 `generated=false` 不代表已认证为人类原创；来源文字仍为未经独立核验的输入。

独立 `list-images` 图表检索暂不接入。正文内已有图片 Markdown/链接保留为来源文字，但不下载图片、不 OCR，也不把图表转换成结构化行情或财务数据。

## 配置

在各电脑自己的私有 env 中配置：

```dotenv
WISBURG_MATERIALS_ENABLED=true
WISBURG_API_KEY=
WISBURG_MAX_CALLS_PER_QUERY=20
WISBURG_MAX_PAGES_PER_CATEGORY=1
```

本机已存在的 `WISBERG_KEY` 名称兼容使用，无需复制或再次填写。两个 Key 字段同时非空但内容不一致时拒绝启用，避免静默选错账户。Key 只放在官方请求头中，不进入 URL、日志、诊断、文档或安装包。公开 `credentials.env.example` 默认不启用；其他电脑用 `IR_SEARCH_CREDENTIALS_FILE` 指向自己的配置。

`list_capabilities().materials` 与 `source_health().configured_sources` 可查看注册/配置状态；目录查询不发网络请求，`live_verified=false` 不能理解为失败，也不能理解为已验收。实际调用状态见请求的诊断。

## SDK 与 MCP

```python
from ir_search import MaterialSearchRequest, MaterialRequest, RequestContext
from ir_search import search_materials, retrieve

result = search_materials(
    MaterialSearchRequest(
        question="美联储政策观点",
        keywords=("美联储",),
        providers=("wisburg",),
        wisburg_categories=("ib", "archive", "article", "mikko"),
        published_start="2026-09-01",
        published_end="2026-09-17",
        candidates_per_source=12,
        text_reads_per_source=3,
        limit=8,
    ),
    context=RequestContext(timeout_seconds=90, max_operations=100),
)

# 从返回结果选择明确的来源引用。每种 URI 都保持无凭证。
if result.items:
    source_ref = result.items[0]["versions"][0]["source_ref"]
    material = retrieve(
        MaterialRequest(question="美联储政策观点", urls=(source_ref,)),
        context=RequestContext(timeout_seconds=60, max_operations=20),
    )
```

MCP 仍调用已有 `search_materials` / `retrieve`，使用同名参数，不增加单独的智堡工具。`wisburg_categories` 可为空，表示按内容类型筛选后依次考虑上述 9 栏；建议调用方明确栏目和 `providers=["wisburg"]`，避免其他来源占用默认来源预算。

引用格式为 `wisburg://report/ID`、`wisburg://article/ID`、`wisburg://mikko/ID`。报告 ID 在不同栏目间会重复，搜索内按同一报告 ID 去重；详情采用官方支持的仅 ID 读取。`retrieve` 的摘要 `text_origin=provider_summary`，文章/日志为 `extracted_text`。摘要引用对应取得的摘要文字，不能写成原作者逐字引语。所有引用保留字符坐标和文本哈希。

`retrieve(..., archive_dir=...)` 可显式保存资料。摘要归档在阅读文件顶部标明“供应商已存摘要、非原始文件”；通用归档仍拒绝没有存储来源标记的生成式答案。`archive_images` 不会自动从智堡 Markdown 中解析或下载图片。

## 确定性检索、时间与限制

- 单次使用一个字面查询：显式 `entities` 的第一项优先，其次 `keywords` 第一项，否则使用完整问题（最多 200 字符）。其他词只参与本地匹配，不调用 LLM 扩词；词组限制通过诊断及 `scans[].search_query` 可见。海外公司别名需调用方明确提供，不能假定已支持全市场证券名称转换。
- 日期传为北京时间当日零点至结束日次日零点，并再次本地过滤。这里是智堡提供的发表时间，不是原始研报出具时间，也不是讨论的业务期间。
- 候选条数由既有 `candidates_per_source` 控制，在栏目间分配；每页最多 20 条，默认每栏 1 页，可配置最多 2 页。子查询记录最多 16 条，限制会明确诊断。只对有剩余候选预算的栏目发请求。
- 每次 `search_materials` 默认最多 20 个业务工具调用，可配置 1–40；详情也计入调用预算。另共享上层总期限和操作次数，握手额外消耗 2 次操作。
- 正文/摘要读取遵守 `text_reads_per_source`。未读取条目保留标题或列表片段；不会把标题当正文。同等词匹配、来源层级时，已有摘要优先于只有标题的结果，全文稍高；这些分值不是事实可信度认证。
- 智堡工具返回文本格式，本实现按已验证的格式严格解析：ID、日期、数量或页脚改变时返回结构诊断，不伪装为空查询。鉴权、权限、额度、频率与网络错误不无限重试。分页游标重复或停滞时停止，并保留已取得结果；没有明确末页标记时不猜测全量覆盖。
- 官方域名固定，验证证书，不跟随带鉴权的重定向；响应最多 4 MiB，工具正文最多 100 万字符，再按调用者的返回上限截断。服务端反向请求不执行，原始错误和凭证回显不会外泄。

核心框架仅对显式声明 `allows_stored_summaries` 的来源允许带生成标记的**已存摘要**进入素材搜索；同时要求 `abstract`、`opinion` 和 `provider_stored_summary`。其他生成式答案依然不作为普通素材进入搜索。现有数值数据路由不变。

真实范围、样本与独立安装结果见[智堡验收记录](wisburg_material_acceptance.md)。
