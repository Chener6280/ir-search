# 当前能力、实测与限制

交接版本 0.2.0rc1，汇总截至 2026-09-18 的既有验收。本轮整理文档没有重新调用所有供应商。`configured_unverified` 仅表示本机配置/依赖，不能抹去历史失败，也不能保证凭证现在有效。每台电脑须按用户选择重新实测。

## 数值服务 get_data

| 来源 / 范围 | 已实现且有有限真实样本 | 必须保留的边界 | 指南 / 验收 |
|---|---|---|---|
| Wind / JYDB | A 股证券、日线、核心三表、国内期货期权日线与合约/日历 | 国内回退限覆盖缺失；不跨源拼字段。JYDB 部分币种未知，期权持仓有已知冲突；不是 PIT 或全科目财报 | [交付](domestic_data_delivery.md) |
| AKShare | 近期 A 股 OHLC、具体期货分钟、ETF 期权当日点价 | 成交量口径、夜盘归属及历史窗口有限；其他期权日内未通过，不编造历史日内 | [数据验收](data_acceptance.md) |
| FMP | 指定美股资料、基础日线、年度标准化三表 | 以已接入端点与账户权限为准；季度/更宽历史/Ultimate 能力未因填 Key 自动实现 | [指南](fmp_adapter.md) / [验收](fmp_adapter_acceptance.md) |
| Wind 基金 / ETF；JYDB 净值备用 | 概况、净值、份额、已披露股票持仓、场内基金行情 | A/C 不合并；持仓非全资产组合；价格/NAV/IOPV 不混用；JYDB 净值字段有限 | [指南](expanded_adapters.md) / [验收](expanded_adapters_acceptance.md) |
| FRED / ECB（global_macro） | 固定目录原频率宏观与汇率序列 | 最新修订快照，非 PIT；观察期不是发布日期，不插值、不自行计算同比 | [指南与目录](expanded_adapters.md) |
| 世界银行（global_macro） | adapter 与离线规则已有 | **本机真实 TLS 连接未通过**，不能标为在线可用 | [失败记录](expanded_adapters_acceptance.md) |
| Fiona | 具体合约日线、近期分钟、源 Greeks/IV | 部分分钟需较长预算；数量/金额/模型单位不明处仍 partial。显式 provider=fiona，不替换国内主源 | [接口和验收](expanded_adapters_acceptance.md) |

## 素材 search_materials / retrieve

所有素材搜索均需显式 `providers`。同一来源不同操作的通过状态不能互相替代。

| provider | 当前用途及真实样本 | 限制 / 接手重点 | 指南 / 最新相关验收 |
|---|---|---|---|
| web | 博查、Exa、兼容 AnySearch 发现；原站读取和精确引用 | 搜索摘录非原文；域名/日期/目录链接分别验证；额度交接由 Agent 执行 | [网页](web_material_adapter.md) / [回退实测](web_search_fallback.md) |
| rss | 配置的 RSS/Atom 快照与有界原文读取 | 非全历史存档，不后台轮询；feed 摘录与正文分开 | [指南](web_toolkit.md) / [验收](web_toolkit_acceptance.md) |
| jydb | A 股公告元数据与正文 | 日期精度/业务期间限制；先指定证券 | [素材说明](material_search.md) |
| tushare_corpus | 研报摘要、新闻、政策正文 | 摘要不当全文；工具、机构、时间及调用数受限 | [指南](tushare_corpus_adapter.md) / [验收](tushare_corpus_acceptance.md) |
| zsxq | 官方集合、时间流、详情、附件发现/读取 | 有限时间流，不声称全库语义搜索；评论和附件另核查预算 | [指南](zsxq_material_adapter.md) / [续取验收](source_usability_acceptance.md) |
| wechat | 账号池文章列表与原文、缓存及续取 | 非公众号全网全集；原站/供应商取得文本区别可见 | [指南](wechat_material_adapter.md) / [续取验收](source_usability_acceptance.md) |
| ima | 知识库、笔记和授权 PDF/PPT/公众号读取 | 需独立权限；目录与当前页余项可续取不等于全库历史分页 | [指南](ima_material_adapter.md) / [验收](source_usability_acceptance.md) |
| wisburg | 智堡官方九类摘要/文章/日志 | 供应商已存 AI 辅助摘要保留标记；目录不代表全部权限 | [指南](wisburg_material_adapter.md) / [验收](wisburg_material_acceptance.md) |
| alphapai | 账号版共享会议摘要与部分转录 | 需浏览器依赖和有效会话；非完整逐字稿，正式 Open API 未接入 | [指南](alphapai_material_adapter.md) / [验收](alphapai_material_acceptance.md) |
| gangtise | 账号版纪要、研报摘要、研究观点 | 新设备认证；不是研报 PDF 全文或完整会议稿，AK/SK MCP 未接入 | [指南](gangtise_material_adapter.md) / [验收](gangtise_material_acceptance.md) |
| xhs | 可选本地后端搜索、主帖及有界评论 | 另启后端、扫码；会偶发超时，续取依赖短期快照 | [指南](xhs_material_adapter.md) / [可靠性验收](platform_reliability_acceptance.md) |
| xueqiu | 有界网页发现；实验性浏览器读取实现 | **自动主帖正文未通过平台验证**；不以登录态或搜索摘录冒充正文可用 | [失败记录](xueqiu_browser_repair.md) |
| eastmoney | 东方财富股吧主帖素材 | UGC 观点；不是行情入口、全帖或全部评论 | [指南与验收](community_video_acceptance.md) |
| video | Bilibili / YouTube 元数据与可取得字幕 | Bilibili 已恢复有限真实读取；平台无字幕/登录/语言/时间异常均可失败；简介不是字幕 | [指南](community_video_adapters.md) / [修复验收](platform_session_repair_acceptance.md) |
| xiaoyuzhou | 单集简介、显式分段 ASR 及缓存 | 搜索不触发 ASR；需火山 Agent Plan 与 FFmpeg；短片段成功非整集转写通过 | [指南](xiaoyuzhou_audio_adapter.md) / [验收](source_usability_acceptance.md) |
| sec | SEC 索引、表单与指定附件原件 | 显式公司/CIK；访问者声明与网络条件；不涵盖所有全球披露市场 | [指南](sec_filings_adapter.md) / [验收](sec_filings_acceptance.md) |
| hkex | 港交所官方标题及选定原件 | 显式港股代码；标题窗口与正文预算有限 | [指南与验收](expanded_adapters_acceptance.md) |
| company_ir | 腾讯、阿里、小米种子目录 | 腾讯正文通过，阿里有目录样本；小米普通 reader 当轮超时，Firecrawl 另有目录样本，不能合并成同一路线通过 | [IR 验收](expanded_adapters_acceptance.md) / [工具对照](search_provider_comparison.md) |

## 可选读取工具

HTTP 是基础路径；Crawl4AI 是可选动态渲染，Scrapling 仅可选解析器，Firecrawl 是显式远程 rawHtml 读取。Tavily 已做对照实测，但尚未纳入新 `web` 搜索路由。不要因存在 Key 或外部参考卡片就宣称已接入功能。

## 尚未承诺

跨电脑实测、GitHub 远端 CI、长期在线率、全市场/全历史覆盖、历史日内、统一 PIT、自动语义向量库、完整视频/音频文稿、自动后台调度、无人值守会话续期和跨进程统一费用账本均不在当前已验收承诺中。`partial`、`unavailable`、未知日期/单位和额度交接是正常契约的一部分；调用 skill 必须向用户披露。
