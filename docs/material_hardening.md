# 机构识别、官方目录与受预算约束的素材检索

2026-09-17。本轮根据金融爬虫文章中的可复用思路，在 `ir_search` 包内独立实现；未复制或安装 `cnfinancialscraper` 代码，不依赖 SkillHub、Desktop/BrokerSkills 或个人绝对路径。核心仍是 `get_data`、`search_materials`、`retrieve`。研究结论、情绪判断、报告编排留给调用方。

## 机构与官方来源目录

首批是 **14 条经官网元数据核对的种子记录**，不是全行业机构库，也不承诺网页都能成功读取：

| ID | 机构 | 类别 |
|---|---|---|
| pbc / csrc / nfra | 中国人民银行 / 中国证监会 / 金融监管总局 | 监管 |
| efunds / chinaamc | 易方达 / 华夏基金 | 基金公司 |
| icbc / cmb | 工商银行 / 招商银行 | 银行 |
| cicc / citics | 中金公司 / 中信证券 | 证券公司 |
| blackrock | 贝莱德 | 海外资管 |
| jpmorgan / goldman_sachs | 摩根大通 / 高盛 | 海外金融机构 |
| federal_reserve / sec | 美联储 / 美国证券交易委员会 | 海外监管 |

`list_institutions()` 返回每条记录的名称、检索别名、类别、地区、官网域名、参考地址、核对日期和已知目录入口。MCP 无需增加工具：现有 `list_capabilities` 的 `materials.institution_catalog` 返回同样信息。资源随 wheel 打包，更新时应逐条核对官网并保留日期。

机构别名用于素材问题解析、词面匹配和网页区域路由。短英文缩写匹配完整词且区分大小写，避免把 `sec` 当作 SEC；长名称优先，避免把“美国证监会”同时识别为中国证监会。“央行”“摩根”“中信”等歧义称呼不强行映射。别名表示检索线索，不表示母子公司、品牌与法人在法律上等同。

普通检索不会自动限制到官网；需要机构原文时显式传 `web_institutions=["csrc"]`。它只限制 `web` adapter，不影响其他来源。请求范围与本机 `WEB_ALLOWED_DOMAINS` 取交集；交集为空时不取数，不自动扩大范围。博查同时发送原生 `include` 域名参数；AnySearch 保留查询词中的 `site:` 提示。结果域名和最终重定向域名都会再次检查。博查参数已通过实际 API 请求确认；第三方索引仍可能漏检或返回站外候选，不能以原生过滤代替本地校验。官网命中只表示域名对应关系；网页内容仍为不可信外部文本。官网上转载的第三方意见仍不能推断成监管认可或独立证实。

## 无网络执行预览

```python
from dataclasses import replace
from ir_search import MaterialSearchRequest, RequestContext, search_materials

request = MaterialSearchRequest(
    question="中国证监会期货公司监督管理办法",
    keywords=("期货公司",),
    published_start="2026-09-01", published_end="2026-09-17",
    providers=("web",), web_institutions=("csrc",),
    candidates_per_source=4, text_reads_per_source=2,
    web_read_mode="http", web_read_workers=2,
    dry_run=True,
)
preview = search_materials(request, context=RequestContext(max_operations=30))
# 审查 preview.plan、coverage、required_inputs、diagnostics 后执行：
result = search_materials(replace(request, dry_run=False),
                         context=RequestContext(timeout_seconds=60, max_operations=30))
```

MCP `search_materials` 支持同名参数。`dry_run=True` 只读取本机配置与包内资源，不访问数据源、做 DNS、登录、鉴权探测或余额查询；不调用第三方 adapter 的“预览”方法。返回的 `items` 为空，`complete=False`，诊断为 `dry_run_no_source_calls`，来源状态为 `planned_not_queried`，不能当作检索覆盖。

预览和执行使用同一套来源选择与网页查询规划。计划包含发布窗口、业务期间、解析出的机构和别名、来源排除原因、网页引擎/检索词/域名、候选及正文上限、操作预算、超时与并发设置。缺少必要范围仍返回 `required_inputs`；配置注册不等于账号可用。普通来源只暴露框架预算，未伪造其内部网络请求数。

`cost_estimate` 和不可知的网络尝试上限为 `null`；正文读取次数不等于 HTTP 请求次数，重定向、浏览器资源、供应商内部查询都可能消耗操作预算。计划不保证费用、耗时、命中数或全量覆盖。已知监管目录链接仅作为调用方参考，不会随预览或搜索自动抓取。

## 目录、附件和正文

已知央行条法司、证监会政府信息公开、金融监管总局规章库目录通过共享网页读取器识别。目录结果标注 `directory_links`、`web_directory_listing_not_document_body` 和 `directory_completeness=not_established_no_pagination`，不归类成某份政策正文。NFRA 未展开的模板表达式被识别为 `loading`，不会交付为证据；`auto` 可以尝试已有的可选 Crawl4AI，失败保留诊断。

链接发现增加 PDF 查询参数/下载文件名、PDF 嵌入和 DOC/DOCX/XLS/XLSX/CSV/ZIP 附件识别。最多保留 100 个链接，PDF 优先，均标为 `discovered_not_retrieved`；素材候选最多附带 50 条附件元数据，超限明确警告。链接及名称不能证明文件可下载、正文完整或发布日期；本轮不增加 Office/OCR 解析。

需要原文时继续使用已有的 `retrieve`，显式给出 URL，或通过 `follow_links`、`link_domains` 允许一层有界跟读。附件正文只在实际下载并提取后产生引用；下载失败、目录、摘要、正文以及未知日期仍分别记录。

```python
from ir_search import MaterialRequest, retrieve

bundle = retrieve(MaterialRequest(
    "期货公司监督管理办法",
    ["https://www.csrc.gov.cn/csrc/c101953/c7658242/content.shtml"],
    web_read_mode="http", follow_links=1, link_domains=("csrc.gov.cn",),
))
```

## 并发与稳定性

默认仍串行。`web_read_mode="http"` 时可设 `web_read_workers=2..4`，只并发已选出的公共网页正文读取。候选和读取名额先按搜索顺序确定，无效/重复/站外 URL 不占正文名额；完成顺序不改变收集顺序。单条读取失败保留该条搜索摘要和失败诊断，其他成功结果继续返回。`auto/browser` 模式保持串行，防止多个浏览器进程争抢共享预算；预览同时显示请求值和实际并发值。

所有线程共享同一个原子操作计数器、取消信号和期限。同一请求的公共 HTTP 层对同一主机相邻请求开始时间至少间隔 0.25 秒；收到 HTTP 429 后，该请求不再对该主机发起新的请求，也不自动重试。已在途请求可能继续结束；此限制不是跨进程/跨请求的账户级限流器。调用方仍应控制自身任务并发。

依然逐次校验公网 DNS 与地址、验证 TLS 和重定向；没有引入可能改变地址安全边界的全局 DNS 缓存或连接池。未增加基础依赖。`deep_research` 保持兼容维护范围。

真实验收结果见 [验收记录](material_hardening_acceptance.md)。
