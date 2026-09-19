# Exa、Tavily、Firecrawl 真实调用对比

测试时间：2026-09-18 本机时间，API 记录 UTC 为 2026-09-19。使用用户本机 `credentials.env` 中的三个 Key，分别调用官方 HTTPS API。没有把 Key、Cookie 或账号写入本报告。

## 结论

- **Exa 值得优先作为海外研究素材搜索候选**：本轮原始文件排在首位的表现最好，液冷白皮书和 SELECT 原始论文也较集中。但出现同一文件多个版本、错报告期、具体标题对应网站首页等问题，不能跳过原文核对。
- **Firecrawl 适合公司 IR 目录和网页原文读取，也有搜索补漏价值**：本轮九个明确原始文件任务，前五条链接命中七题；小米 H1 官方公告是另外两家没有找到的结果。取得小米 IR 报告链接，补上此前本机直接读取超时的样本。公众号读取则未成功。
- **Tavily 本轮通用高级搜索不适合直接取代现有主搜索，但正文读取值得保留为候选**：成功取得英伟达财报正文和用户指定公众号文章；小米目录虽返回 HTTP 200，却漏掉报告链接。新闻专用模式、日期/域名调优和长期稳定性尚未测试。
- 建议下一步采用“Exa 搜索候选 + Firecrawl 搜索补漏/官网读取 + Tavily 指定网页补读”的可选分工。本轮没有正式增加 Exa/Tavily adapter，没有更换博查/AnySearch，没有修改默认联网路由。

## 样本与方法

每家 12 个完全相同的查询，每题最多 5 个结果，最终各取得 60 条。英文 8 题、中文 4 题。选历史明确时期的问题，便于识别年份与财年混淆；本轮不评价最近七天新闻覆盖。

| ID | 实际发送的查询 |
|---|---|
| S01 | NVIDIA second quarter fiscal 2026 results Blackwell gross margin |
| S02 | Microsoft fiscal 2025 fourth quarter earnings Azure capital expenditures |
| S03 | Tencent 2025 second quarter results advertising games revenue |
| S04 | Federal Reserve September 2025 FOMC statement target range |
| S05 | ECB June 2025 monetary policy decisions deposit facility rate |
| S06 | data center direct to chip liquid cooling technical white paper CDU reliability |
| S07 | GLP-1 semaglutide cardiovascular outcomes SELECT trial publication |
| S08 | copper mine supply disruption 2025 Indonesia Grasberg force majeure |
| S09 | 贵州茅台 2025年9月 动销 批价 渠道 库存 |
| S10 | 中国 2025年8月 社会融资规模 增量 央行 |
| S11 | 沪深300ETF 510300 2025 半年度报告 基金 持仓 |
| S12 | 小米集团 2025 中期业绩 汽车业务 毛利率 |

搜索参数：Exa `auto`；Tavily `advanced`、`general`，关闭答案、自动参数和正文附带；Firecrawl `/v2/search`、`sources=[web]`，不附带抓取。不指定白名单、日期过滤、国家或语言，均依赖查询本身。三者不是同价位、同索引或同搜索算法；这是上述配置的有限样本比较。

原文测试为同样六个公开 URL：美联储声明、英伟达财报、腾讯 IR、小米 IR、阿里 IR、用户给定公众号文章。Exa 首轮要求 `maxAgeHours=0`；Firecrawl 要求 `maxAge=0`、禁缓存写入、basic proxy、HTML/Markdown；Tavily 使用 advanced extract，其接口没有本轮可比较的强制刷新证明。后验另测 Exa 默认缓存及目录 `extras.links`。不把缓存结果当成刚从原站抓取。

未请求生成答案、研究报告、JSON 生成式字段或模型综合结论。并行仅跨供应商，单个供应商各阶段顺序调用；搜索和读取阶段有时间重叠。未自动重试收费请求。

## 搜索结果

| 指标 | Exa | Tavily | Firecrawl |
|---|---:|---:|---:|
| 最终取得非空结果的查询 | 12/12 | 12/12 | 12/12 |
| 成功搜索请求耗时中位数 | 2.105 秒 | 3.702 秒 | 2.163 秒 |
| 九题明确原始文件任务：首位链接命中 | 6/9 | 1/9 | 1/9 |
| 同九题：前五条至少一条链接命中 | 6/9 | 4/9 | 7/9 |

这里的“命中”是事后人工核对标题/URL 所做的**指定原始文件链接定位**，不是全文真实性、事实正确率、总体相关性或完整召回率。九题包含公司指定季度披露、央行指定会议/月份文件和 SELECT 原始结果论文；官网首页、转载、错时期和联接基金不计命中。不将重复的官网/SEC/PDF/DOI 版本当成独立证据。未逐篇读取全部 180 条搜索结果，也未与 AnySearch/博查同题实测，不能据此认定已胜过现有主路由。

| 明确文件任务 | Exa 首次命中排名 | Tavily 首次命中排名 | Firecrawl 首次命中排名 |
|---|---:|---:|---:|
| S01 NVIDIA FY2026 Q2 | 1 | — | 2 |
| S02 Microsoft FY2025 Q4 | 1 | 3 | 4 |
| S03 Tencent 2025 Q2 | 1 | 2 | 2 |
| S04 2025-09-17 FOMC 声明 | 1 | 1 | 5 |
| S05 2025-06-05 ECB 决议 | 1 | 2 | 2 |
| S07 SELECT 原始结果论文 | 1 | — | 1 |
| S10 央行 2025 年 8 月社融增量原文 | — | — | — |
| S11 510300 本基金 2025 中报原文 | — | — | — |
| S12 小米 2025 H1 官方业绩公告 | — | — | 2 |

其他有用观察：

- 液冷 S06：Exa 找到 OCP 的 CDU 性能/测试资料和纬颖白皮书；Tavily 找到厂商白皮书介绍、纬颖及社交链接；Firecrawl 找到 Vertiv 技术文章、OCP 白皮书和施耐德介绍。只能据样本讨论新增素材，不能宣称 Exa 普遍覆盖所有技术资料。
- 茅台 S09：Exa 与 Tavily 首位均为 21 经济网 2025-09-26 的动销报道；Tavily 还找到 2025-09-30 的券商 PDF 链接，具有补充价值。Firecrawl 首位是 2026 年股东业绩沟通材料，目标时期的报道出现在第 5 位。
- 英伟达 S01：三家都混入 FY2027/2026 自然年材料；Firecrawl 首位即为 FY2027。财年、自然年与报告期不能只凭搜索排名判断。
- Exa SELECT 结果有 NEJM、DOI、PDF、PubMed 等同一研究的多个入口；铜矿 S08 的具体新闻标题对应 FCX IR 首页，ETF S11 也有具体标题配网站首页的问题，必须验证文章定位。
- 社融和 ETF 题目的原始文件均未命中，说明官方 adapter、宏观序列和基金数据库仍有必要。

Firecrawl 初轮第 11 个搜索请求返回 **429**，错误明确当前账号 **10 次/分钟** 限制。测试初轮发速超过该限制，随后停止该阶段；超过恢复窗口后，以至少 7 秒间隔补测最后两题，均成功。共发出 13 次搜索 HTTP 请求，有 12 次成功、1 次限流。中位耗时只统计成功请求，不含冷却时间；这不是长期可靠性统计。

## 原文与目录读取

| 样本 | Exa 首轮要求刷新 | Tavily advanced | Firecrawl basic |
|---|---|---|---|
| 美联储 2025-09-17 声明 | 2,865 字符；声明、利率段和投票段存在 | 4,854 字符；同样关键段存在 | 3,681 字符 Markdown；同样关键段存在 |
| NVIDIA FY2026 Q2 财报 | HTTP 200 内嵌 `CRAWL_UNKNOWN_ERROR`，无正文 | 38,801 字符，核心指标和现金流表标题存在 | 41,677 字符 Markdown，核心指标和现金流表标题存在 |
| 腾讯 IR 目录 | 808 字符，text 模式未附报告链接 | 18 个不同 PDF 链接 | 18 个不同 PDF 链接 |
| 小米 IR 目录 | HTTP 200 内嵌 `CRAWL_TIMEOUT` | 522 字符，标题/图片存在但无报告链接 | 取得 2025 年报及中报两个下载链接 |
| 阿里 IR 目录 | 347 字符，只有报告名称，未附下载链接 | 13 个不同 PDF 链接 | 13 个不同 PDF 链接 |
| 用户指定公众号文章 | 6,007 字符 | 5,358 字符 | 超过 120 秒仍未获得完整响应，终止请求 |

字符数受 HTML 清洗、导航、Markdown 链接格式影响，**不表示越长越完整**。目录取得链接不等于已下载报告正文。公众号两家正文标题、多个章节一致，未做原站全文逐字完整性认证，不能推导公众号普遍可读、账号历史文章可找全或可越过登录限制。

Exa 后验补测：

- NVIDIA 默认缓存模式返回 33,614 字符，响应显式为 `source=cached`；没有抹掉首轮刷新失败。
- 腾讯增加 `extras.links=100` 后，从缓存返回 18 个不同 PDF 链接，说明首轮 text 未附链接不能解释为 Exa 完全不会返回链接。
- 小米默认缓存模式加 links 返回 2025 年报和中报链接；同样标记 `cached`，不算刷新成功。
- 阿里默认缓存加 links 返回 324 字符，链接仍为空。

Firecrawl 公众号请求传入 45 秒上游超时；测试 HTTP 客户端设置 70 秒读取空闲超时，但仍超过两分钟未完成，因而终止。原因未确证；持续接收数据可能使空闲超时无法限制总耗时。该样本没有成功正文，费用未知。项目正式传输层已有请求总时限与 socket 中断机制，本轮没有放宽该机制。

实际使用的链接：

- [美联储声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm)
- [NVIDIA 财报](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2026)
- [腾讯 IR](https://www.tencent.com/investors/results/)
- [小米 IR](https://ir.mi.com/financial-information/annual-interim-reports)
- [阿里 IR](https://www.alibabagroup.com/en-US/ir-financial-reports-financial-results)
- [公众号样本](https://mp.weixin.qq.com/s/mmsc5m2wPhHiPgfgzUZ-_Q)

## ir_search 现有链路验证

通过真实 `retrieve(..., web_read_mode='firecrawl')` 另测美联储与小米，各返回一份材料。仅该测试进程临时启用 Firecrawl，测试配置文件已删除，永久默认开关未改变。

- 美联储：原站身份为 REGULATOR，10 条引用全部与返回文本字符位置吻合。
- 小米：原站身份为 COMPANY，6 条引用全部与返回文本字符位置吻合；年报 `.pdf` 与中报 `/static-files/` URL 均保留在链接清单，中报 URL 当前通用分类为 `web_page`。
- 两份结果均为 `partial`，保留发布日期未知、抽取警告；美联储还有链接截断提示。Firecrawl 远程跳转不可逐跳观察、HTML hash 是供应商返回内容等诊断继续可见。
- 小米样本是目录，通用规则当前标为 `article_text`，不能据此把它当成财报正文。正式扩展目录采集时应加强目录分类和无扩展名附件识别。

## 用量与凭证

本轮供应商 API 请求 61 次：搜索 37 次（含 429 一次）、原文首轮 18 次（含终止一次）、Exa 补测 4 次、Firecrawl SDK 读取 2 次。最初受本机沙箱阻止的三次连接没有取得 API 响应，另行保存，不计入供应商请求完成统计。请求次数不能直接推断账单。

供应商计费字段：Exa 已完成 12 次搜索 + 6 次首轮读取 + 4 次补测，`costDollars.total` 合计 **0.092 美元**；Tavily 12 次搜索返回 **24 积分**，6 次读取返回字段合计 **2 积分**；Firecrawl 12 次成功搜索返回 **24 积分**，5 次完成的首轮抓取返回 **5 积分**。Firecrawl 两次 SDK 读取没有向上暴露 credits，终止的公众号请求也未返回计费结果。Tavily 单条 usage 为 0 不代表该能力永久免费。

这些是返回的用量字段，可能使用免费额度，不是信用卡实际扣款或完整账单。未访问账单控制台、未购买/升级套餐。各家积分不能直接等价比较。[Exa 计价](https://exa.ai/docs/admin/pricing)、[Tavily 计价](https://docs.tavily.com/documentation/api-credits)、[Firecrawl 计价](https://www.firecrawl.dev/pricing)。

本地 env 中重复的空 `FIRECRAWL_API_KEY` 已移除，用户填写的非空项保留，权限仍为 0600。三家认证均真实通过。本轮详细响应、查询清单、人工链接核对和脚本仅保存在 Git 忽略的 `.local/search-provider-comparison/`，不进入安装包。

回归验证：`python3 -m pytest` 为 **2,021 passed、21 skipped**，5 条既有 PDF/SWIG 弃用警告。私有响应及新报告的凭证值扫描通过，临时 env 已删除。本轮未修改生产代码，未提交或推送 GitHub。

## 接入时应保留的边界

1. 分开记录搜索提供方、原文发布方、文字获取工具，官方来源等级由最终来源决定，不由 Exa/Firecrawl 品牌决定。
2. 年份/财年/报告期、官网首页误匹配、同一文件多个版本，都需要确定性检查；缺日期不得凭查询补造。
3. Exa 的 `cached/crawled`、Tavily 的逐 URL 失败数组、Firecrawl 的限流和总超时分别记录；HTTP 200 不升级为正文成功。
4. 启用计费工具需显式预算和可见用量，不默认每题同时调用三家；不调用生成式答案来替代原文引用。
5. 先做独立、可选、可关闭的接口，再与博查/AnySearch 同题比较；这次测试不足以证明应替换现有主路由。

接口参考：[Exa Search](https://exa.ai/docs/reference/search)、[Exa Contents](https://exa.ai/docs/reference/get-contents)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)、[Firecrawl Search](https://docs.firecrawl.dev/features/search)、[Firecrawl Scrape](https://docs.firecrawl.dev/features/scrape)。
