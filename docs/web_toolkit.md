# 网页读取与 RSS 素材发现

本轮实现全部位于可安装的 `ir_search` 包内；`get_data` 和兼容用 `deep_research` 不变。

| 参考项目 | 采用范围 | 调用位置 |
|---|---|---|
| [Firecrawl](https://github.com/firecrawl/firecrawl) | 官方 v2 scrape API，仅 rawHtml，无需 Firecrawl SDK | 显式 `web_read_mode="firecrawl"` |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | 0.4.15 HTML Selector，唯一 article/main 正文区域，网络仍用本项目 HTTP 客户端 | 显式 `web_read_mode="scrapling"` |
| [TrendRadar](https://github.com/sansan0/TrendRadar) | 参考 RSS 发现、关键词过滤、去重；独立编写 RSS/Atom adapter | `search_materials(providers=["rss"])` |

没有复制上游运行源码。固定 revision 的 README、许可证和采用记录保存在本地参考库，不进入 wheel。没有引入 TrendRadar 的 AI 分析、推送或后台调度；Scrapling 不安装 `ai`、`fetchers` 或 `all` extras，不引入其 MCP 服务。

## 配置

每台电脑使用自己的私有 `credentials.env`，或通过 `IR_SEARCH_CREDENTIALS_FILE` 指定它。macOS/Linux 文件权限需为 0600。公开模板默认关闭新能力。

```dotenv
RSS_ENABLED=true
RSS_FEED_URLS='["https://www.federalreserve.gov/feeds/press_all.xml", "https://www.ecb.europa.eu/rss/press.html"]'
FIRECRAWL_ENABLED=false
FIRECRAWL_API_KEY=
```

开发电脑已配置并启用上面两个公开订阅源，由[美联储订阅说明](https://www.federalreserve.gov/feeds/feeds.htm)和[欧洲央行订阅说明](https://www.ecb.europa.eu/home/html/rss.en.html)确认。其他电脑可以选择自己的公开 HTTPS RSS/Atom 地址，最多五个，不支持认证 URL。RSS 不需要新依赖或 Key。

Scrapling 在 Python 3.10+ 中安装 `ir-search[scrapling,mcp]`；源码安装使用 `python -m pip install '.[scrapling,mcp]'`。基础安装仍支持 Python 3.9；缺少可选包时显式选择 Scrapling 返回 `dependency_missing`，不会自动安装或换后端。开发环境的实测安装不代表其他 Python/MCP 进程已经安装此依赖。

Firecrawl 需填入 Key、设置 `FIRECRAWL_ENABLED=true`，并在每次调用选择 `web_read_mode="firecrawl"`。仅填入 Key 不会启用。它可能按供应商套餐计费；默认 `auto` 不会调用它。初次集成仅完成离线协议验证；2026-09-18 后续实测已取得美联储和小米 IR 材料，均保留 `partial` 诊断，公众号样本读取未成功。此次只在测试进程临时启用，详情见[三家真实调用对比](search_provider_comparison.md)。

## Skills / SDK / MCP 调用

```python
from ir_search import MaterialSearchRequest, RequestContext, search_materials

result = search_materials(
    MaterialSearchRequest(
        question="inflation expectations",
        keywords=("inflation", "expectations"),
        providers=("rss",),
        published_start="2026-09-01",
        published_end="2026-09-18",
        candidates_per_source=20,
        text_reads_per_source=2,
        web_read_mode="scrapling",
    ),
    context=RequestContext(timeout_seconds=90, max_operations=10),
)
```

MCP `search_materials` 使用同名字段。`text_reads_per_source=0` 只扫描订阅元数据/摘要；大于零时受限读取候选文章。`dry_run=true` 不联网，展示 feed 数、正文读取上限、显式云端读取上限，费用仍为未知而非零。

选择结果里的 `original_url` 后，沿用 `retrieve` 读取原文及引用。通用网页支持 `http/auto/browser/scrapling/firecrawl`；`auto` 保持原行为：普通 HTTP，只有观察到动态加载缺口才尝试可选 Crawl4AI。新模式只用于通用网页；公众号等专用读取链路保持独立，公众号显式收到这两个新模式会返回不支持诊断。

## RSS 的数据口径

- 支持 RSS 2.0、Atom 1.0；不自动下载附件或播客 enclosure。
- 每条 feed 限 2 MiB、最多解析 200 条，最多三次跳转；逐跳检查公网地址，禁止 HTTPS 降级。DTD/实体、过深 XML、过大响应都会失败并返回诊断。
- 在 feed 间分配候选扫描预算。只读当前快照，不声称覆盖指定时期的全部文章，不追溯历史、不返回伪造续页游标。`complete=false` 和 `rss_snapshot_not_historical_archive` 持续可见。
- `pubDate` / Atom `published` 是发布日期来源，`updated` 不冒充发布日。无日期保留但提示未知；取得正文日期后使用正文元数据，并报告与 feed 的冲突。抓取时间另列。
- 先按 feed 日期、显式关键词筛选，再按预算读取正文；最终匹配沿用核心的确定性实体/主题规则。无法保证找全仅在未读正文出现的关键词。
- 一次扫描内按文章 URL 去重，不把多个 feed 当作独立印证。已有材料归档/运行记录可供 skills 比较运行；本 adapter 不创建定时任务或推送。
- 摘要是 `source_excerpt`，文字提供方为 `rss`，`source_ref` 指向带条目标识的 feed，`original_url` 指向待核对文章。只有原文读取成功才变为 `extracted_text`，文字提供方为 `web`。摘要不会因链接指向官网就升级为已核实官方正文。
- 空标题、无效链接等条目被过滤并报告 `invalid_rss_entry`。正文失败保留摘要及失败原因；验证码文字不能变成文章证据。

## 正文读取边界

Scrapling 只选唯一 `article`，否则选唯一 `main` / `role=main`。多篇、空区域、过短正文、目录或挑战页保留普通解析结果，记录 `html_extraction.state`、`selected`、`scrapling_selection_fallback`。不启用自适应相似匹配、stealth fetcher，不声称修复雪球验证限制。HTML 元数据和链接保留整页结果；正文哈希和引用位置按最终返回文字重算。选择器命中不等于完整性验证。[Selector 文档](https://scrapling.readthedocs.io/en/latest/parsing/main_classes.html)

Firecrawl 固定调用官方 HTTPS `/v2/scrape`，Bearer Key 只发给该 API；不给目标网站传 Cookie，不发送带查询参数/fragment 的 URL。只请求 rawHtml，关闭 PDF 解析、TLS 跳过和缓存复用，使用 basic proxy，不请求 JSON/Agent/LLM 提取。API 重定向不跟随。验证原站 HTTP 状态、最终 URL 和域名范围；缺少最终 URL 或状态时不接受为原文。[官方接口](https://docs.firecrawl.dev/api-reference/endpoint/scrape)

远端中间跳转/DNS 不受本机逐跳控制；本机只检查初始和报告的最终地址，`firecrawl.redirect_scope` 标记为 `remote_intermediate_hops_unobserved`。返回 HTML hash 不是原站网络字节校验，接收时间不是发布时间。需要严格本机域名边界时继续使用本地 HTTP/浏览器。本轮未配置自托管 Firecrawl。

`list_capabilities.web_reading.optional_readers` 和 `source_health` 提供配置/依赖诊断，不会把“Key 已填”“包已安装”标记为实测成功。验收结果见 [本轮验收](web_toolkit_acceptance.md)。
