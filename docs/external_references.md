# 外部项目、技能和依赖清单

本清单用于跨电脑交接，依据此前已审阅的本地快照和接口验收；本轮没有重新下载或宣称上游最新版。机器可读版本为 [external_references.json](external_references.json)：48 条本地参考记录，加 9 条服务接口，共 57 条。

## 主要外部项目

| 项目 | 采用状态 | 本项目中的用途 | 审阅 revision / 许可证快照 |
|---|---|---|---|
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | optional_dependency | 已安装实测；借鉴可选动态渲染、非 LLM 提取；未将其作为默认正文热路径 | 安装约束 0.9.3；本表不作许可证认定 |
| [firecrawl](https://github.com/firecrawl/firecrawl) | optional_service_client | 官方 rawHtml API，显式启用、无 LLM、记录远端读取边界。 | e3e324e3d425；AGPL-3.0 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | optional_dependency | 只采用 HTML Selector；保留唯一正文选择和失败诊断，不启用 stealth、自适应匹配或 AI/MCP。 | 2b160ee18bfe；BSD-3-Clause |
| [TrendRadar](https://github.com/sansan0/TrendRadar) | design_reference | 借鉴 RSS 素材发现、关键词过滤和去重；独立实现 RSS/Atom，不复制运行源码、不迁入调度/推送/AI。 | 792bcc3928b1；GPL-3.0 |
| [Agent-Reach](https://github.com/Panniantong/Agent-Reach) | design_reference | 参考来源体检、后端声明、已配置与已验证分离；包内独立实现doctor与修复规则，不执行安装器。 | a19a171fa980；MIT |
| [web-ui](https://github.com/browser-use/web-ui) | design_reference | 浏览器会话与人工登录边界；用于来源诊断和恢复建议，不引入LLM浏览器主链路。 | 61962296c38a；MIT |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | design_reference | 参考采集/存储分离、主帖评论结构和配置预算；未复制实现，非商业学习许可证不作为部署依赖。 | 8ecfa31de22c；NON-COMMERCIAL LEARNING LICENSE 1.1 |
| [xiaohongshu-mcp-v2.5.0](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.0/docs/API.md) | external_optional_backend | v2.5.0只读HTTP登录状态/搜索/详情契约；包内独立实现凭证隔离、缓存、预算、引用及诊断。 | v2.5.0；Apache-2.0 |
| [YouTube Transcript API](https://github.com/jdepoix/youtube-transcript-api) | optional_dependency | 本轮视频字幕参考和可选依赖；无字幕或访问受限时不生成替代文稿 | 安装约束 1.2.4；本表不作许可证认定 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | design_reference | 仅评估字幕/元数据设计；本轮不执行视频或音频下载 | 未记录固定 revision；本表不作许可证认定 |
| [podcast-summary / wechat-to-md](https://github.com/hxer7963/podcast-summary/tree/main/.codebuddy/skills/wechat-to-md) | design_reference | 参考文章、元数据与图片分开存储；独立实现，未执行外部 skill | 未记录固定 revision；本表不作许可证认定 |
| [podcast-summary / podcast-fetch](https://github.com/hxer7963/podcast-summary/tree/main/.codebuddy/skills/podcast-fetch) | design_reference | 参考单集元数据/简介与音频地址分层；Agent Plan 使用另一种流式接口，未执行上游脚本 | 未记录固定 revision；本表不作许可证认定 |
| [WikiSkill article and paper](https://arxiv.org/abs/2608.27454v1) | design_reference | 明确采用范围的参考卡片；运行摘要、已审阅恢复规则、回归验证分离。 | 未记录固定 revision；本表不作许可证认定 |

许可证列仅记录被审阅快照的标识，不代表上游当前版本。MediaCrawler 未复制运行实现、未作为部署依赖；Firecrawl 使用远程 API，没有打包服务端；TrendRadar 仅参考 RSS 分层。引入上游代码或对外分发时应复核实际采用版本。

## 全量参考记录

`adapted` 表示独立重构采用能力/约束，不代表复制原技能；`design_reference` 是设计参考；`reviewed_only` 未接入；`service_client` 是独立接口客户端；`optional_dependency` 是按需安装的库；`external_optional_backend` 是独立后端；`optional_service_client` 是可选远程服务。

| ID / 名称 | 状态 | 出处 | 实现 / 说明 |
|---|---|---|---|
| s045 / jydb-remote-query | adapted | 本地技能参考，不随包分发 | `ir_search/adapters/jydb.py`、`ir_search/adapters/jydb_market.py`；数据库只读取数与表字段映射 |
| s048 / akshare | adapted | 本地技能参考，不随包分发 | `ir_search/adapters/akshare_intraday.py`；近期日内行情接口思路；实际实现独立封装 |
| s049 / alphapai-research | reviewed_only | 本地技能参考，不随包分发 | 无运行实现；正式API与账号接入分开；仅参考语料和生成观点分层，官方API尚未接入，账号版共享会议已独立实现。 |
| s058 / news-aggregator-skill | reviewed_only | 本地技能参考，不随包分发 | 无运行实现；新闻来源取材流程与来源分层 |
| s064 / wind-find-finance-skill | reviewed_only | 本地技能参考，不随包分发 | 无运行实现；Wind/iFind 能力盘点；未迁入原 MCP 调用器 |
| s065 / wind-mcp-skill | reviewed_only | 本地技能参考，不随包分发 | 无运行实现；Wind MCP 能力盘点；本项目采用独立 MySQL adapter |
| s069 / xueqiu-scraper | adapted | 本地技能参考，不随包分发 | `ir_search/infrastructure/community_documents.py`、`ir_search/infrastructure/xueqiu_browser.py`、`ir_search/infrastructure/_xueqiu_browser_worker.py`、`ir_search/infrastructure/_xueqiu_tunnel.py`；雪球主帖选择器与浏览器渲染；保留来源行，独立实现可选浏览器读取和预算/诊断；自动主帖实测仍受验证阻断 |
| s070 / yfinance | reviewed_only | 本地技能参考，不随包分发 | 无运行实现；海外数据候选；当前主数据源为 FMP |
| s072 / zsxq-scraper | adapted | 本地技能参考，不随包分发 | `ir_search/adapters/zsxq_materials.py`；星球选择、时间流、帖子与附件取材 |
| s086 / announcement-filter | adapted | 本地技能参考，不随包分发 | `ir_search/services/announcements.py`；公告标题按证券与时间筛选 |
| s087 / ashare-data | adapted | 本地技能参考，不随包分发 | `ir_search/adapters/wind_mysql.py`、`ir_search/adapters/financial_statements.py`；Wind MySQL 单表查询与数据字典 |
| s092 / mutual-fund-data | reviewed_only | 本地技能参考，不随包分发 | 无运行实现；基金库与口径参考；不能视作全基金接口已迁入 |
| zsxq-shared / zsxq-shared | adapted | [公开来源](https://github.com/unnoo/zsxq-skill)；[公开来源](https://garden.zsxq.com/skill/) | `ir_search/infrastructure/zsxq.py`；官方接入能力与帖子/集合行为参考 |
| zsxq-topic / zsxq-topic | adapted | [公开来源](https://github.com/unnoo/zsxq-skill)；[公开来源](https://garden.zsxq.com/skill/) | `ir_search/infrastructure/zsxq.py`；官方接入能力与帖子/集合行为参考 |
| zsxq-group / zsxq-group | adapted | [公开来源](https://github.com/unnoo/zsxq-skill)；[公开来源](https://garden.zsxq.com/skill/) | `ir_search/infrastructure/zsxq.py`；官方接入能力与帖子/集合行为参考 |
| ima-0 / IMA 官方 SKILL.md | adapted | [公开来源](https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip) | `ir_search/infrastructure/ima.py`、`ir_search/infrastructure/ima_documents.py`；官方只读知识库、笔记与授权资源接口 |
| ima-1 / IMA 官方 knowledge-base/SKILL.md | adapted | [公开来源](https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip) | `ir_search/infrastructure/ima.py`、`ir_search/infrastructure/ima_documents.py`；官方只读知识库、笔记与授权资源接口 |
| ima-2 / IMA 官方 knowledge-base/references/api.md | adapted | [公开来源](https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip) | `ir_search/infrastructure/ima.py`、`ir_search/infrastructure/ima_documents.py`；官方只读知识库、笔记与授权资源接口 |
| ima-3 / IMA 官方 notes/SKILL.md | adapted | [公开来源](https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip) | `ir_search/infrastructure/ima.py`、`ir_search/infrastructure/ima_documents.py`；官方只读知识库、笔记与授权资源接口 |
| ima-4 / IMA 官方 notes/references/api.md | adapted | [公开来源](https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip) | `ir_search/infrastructure/ima.py`、`ir_search/infrastructure/ima_documents.py`；官方只读知识库、笔记与授权资源接口 |
| crawl4ai / Crawl4AI | optional_dependency | [公开来源](https://github.com/unclecode/crawl4ai) | `ir_search/infrastructure/web_browser.py`；已安装实测；借鉴可选动态渲染、非 LLM 提取；未将其作为默认正文热路径 |
| wechat-to-md / podcast-summary / wechat-to-md | design_reference | [公开来源](https://github.com/hxer7963/podcast-summary/tree/main/.codebuddy/skills/wechat-to-md) | `ir_search/documents/wechat_html.py`、`ir_search/services/material_archive.py`；参考文章、元数据与图片分开存储；独立实现，未执行外部 skill |
| cnfinancialscraper / cnfinancialscraper 文章设计参考 | design_reference | [公开来源](https://mp.weixin.qq.com/s/tLXt6iKMP18lylI3tVMDmQ) | `ir_search/institutions.py`、`ir_search/infrastructure/web_material_plan.py`；仅借鉴机构目录、附件、受预算约束并发与 dry_run 思路；未取得/复制 skill 实现，不采用作者宣称的覆盖率 |
| wisburg / 智堡 Agent 官方接入 | adapted | [公开来源](https://www.wisburg.com/#agent) | `ir_search/infrastructure/wisburg.py`；官方工具与返回字段核验；摘要与原文分开 |
| tushare-corpus / Tushare 大模型语料 | adapted | [公开来源](https://tushare.pro/weborder/#/activity/25) | `ir_search/adapters/tushare_corpus.py`；官方语料只读工具与认证；研报摘要、新闻、政策分层 |
| youtube-transcript-api / YouTube Transcript API | optional_dependency | [公开来源](https://github.com/jdepoix/youtube-transcript-api) | `ir_search/infrastructure/video_documents.py`；本轮视频字幕参考和可选依赖；无字幕或访问受限时不生成替代文稿 |
| yt-dlp / yt-dlp | design_reference | [公开来源](https://github.com/yt-dlp/yt-dlp) | 无运行实现；仅评估字幕/元数据设计；本轮不执行视频或音频下载 |
| podcast-fetch / podcast-summary / podcast-fetch | design_reference | [公开来源](https://github.com/hxer7963/podcast-summary/tree/main/.codebuddy/skills/podcast-fetch) | `ir_search/infrastructure/audio_documents.py`；参考单集元数据/简介与音频地址分层；Agent Plan 使用另一种流式接口，未执行上游脚本 |
| volc-agent-plan-audio / 火山 Agent Plan 接入语音模型 | adapted | [公开来源](https://ark.volcengine.com/region:cn-beijing/docs/82379/2516286) | `ir_search/infrastructure/audio_asr.py`；2026-09-17 已通过浏览器核对，页面更新 2026-09-14；采用 Plan 专属 Seed ASR 2.0 流式认证与资源标识，不采用普通录音 URL 异步 API。仅保存参考卡片。 |
| volc-streaming-asr / 火山流式 ASR 协议 | adapted | [公开来源](https://docs.volcengine.com/docs/6561/1354869) | `ir_search/infrastructure/audio_asr.py`；Agent Plan 文档链接的协议，2026-09-17 已核对；独立实现二进制帧、最终结果、句段时间、200ms PCM 包；关闭语义顺滑，不复制示例日志输出。仅保存参考卡片。 |
| alphapai-transcript / Alpha派共享纪要与本人转记技能 | adapted | 本地技能参考，不随包分发 | `ir_search/adapters/alphapai_materials.py`、`ir_search/infrastructure/alphapai.py`、`ir_search/infrastructure/_alphapai_worker.py`、`ir_search/infrastructure/alphapai_documents.py`、`ir_search/infrastructure/alphapai_cache.py`；独立重构账号登录与共享会议列表/详情；修正完整时间参数、轮换ID及高亮标题，区分存量AI摘要与部分JSON转录，增加私有缓存与安全进程协议。个人录音未启用。 |
| alphapai-official-api / Alpha派官方 Open API / v1.3 安装说明 | reviewed_only | [公开来源](https://open-api.rabyte.cn/alpha/open-api/v1/file/api-docs/install.md) | `docs/alphapai_evaluation.md`；读取说明并只读比较v1.3包；包内三个核心文本与本地v1.2一致，未安装。专用Key未验证，recall片段不等于全文。 |
| gangtise-web-account / 岗底斯官方网页账号素材接口 | adapted | [公开来源](https://open.gangtise.com/#/login) | `ir_search/infrastructure/gangtise.py`、`ir_search/infrastructure/gangtise_auth.py`、`ir_search/infrastructure/gangtise_documents.py`、`ir_search/adapters/gangtise_materials.py`；按官方网页客户端和有限真实请求核对正常账号认证、纪要/研报摘要/观点接口；独立实现，未混用官方 AK/SK MCP。 |
| xhs-insight-agent / xhs-insight-agent | design_reference | 本地技能参考，不随包分发 | `ir_search/adapters/xhs_materials.py`、`ir_search/infrastructure/xhs.py`、`ir_search/infrastructure/xhs_documents.py`；仅借鉴原始互动数、快照与结论分离、明确元数据范围；未采用AI分析、报告或飞书推送。 |
| xiaohongshu-mcp-v2.5.0 / xiaohongshu-mcp-v2.5.0 | external_optional_backend | [公开来源](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.0/docs/API.md) | `ir_search/adapters/xhs_materials.py`、`ir_search/infrastructure/xhs.py`、`ir_search/infrastructure/xhs_documents.py`；v2.5.0只读HTTP登录状态/搜索/详情契约；包内独立实现凭证隔离、缓存、预算、引用及诊断。 |
| review-web-ui / web-ui | design_reference | [公开来源](https://github.com/browser-use/web-ui) | `ir_search/services/source_diagnostics.py`、`ir_search/services/material_runs.py`、`ir_search/infrastructure/recovery.py`；浏览器会话与人工登录边界；用于来源诊断和恢复建议，不引入LLM浏览器主链路。 |
| review-mediacrawler / MediaCrawler | design_reference | [公开来源](https://github.com/NanmiCoder/MediaCrawler) | `ir_search/services/source_diagnostics.py`、`ir_search/services/material_runs.py`、`ir_search/infrastructure/recovery.py`；参考采集/存储分离、主帖评论结构和配置预算；未复制实现，非商业学习许可证不作为部署依赖。 |
| review-agent-reach / Agent-Reach | design_reference | [公开来源](https://github.com/Panniantong/Agent-Reach) | `ir_search/services/source_diagnostics.py`、`ir_search/services/material_runs.py`、`ir_search/infrastructure/recovery.py`；参考来源体检、后端声明、已配置与已验证分离；包内独立实现doctor与修复规则，不执行安装器。 |
| wikiskill-design / WikiSkill article and paper | design_reference | [公开来源](https://arxiv.org/abs/2608.27454v1) | `ir_search/services/material_runs.py`、`ir_search/infrastructure/recovery.py`；明确采用范围的参考卡片；运行摘要、已审阅恢复规则、回归验证分离。 |
| review-firecrawl / firecrawl | optional_service_client | [公开来源](https://github.com/firecrawl/firecrawl) | `ir_search/infrastructure/web_toolkit.py`；官方 rawHtml API，显式启用、无 LLM、记录远端读取边界。 |
| review-scrapling / Scrapling | optional_dependency | [公开来源](https://github.com/D4Vinci/Scrapling) | `ir_search/infrastructure/web_toolkit.py`；只采用 HTML Selector；保留唯一正文选择和失败诊断，不启用 stealth、自适应匹配或 AI/MCP。 |
| review-trendradar / TrendRadar | design_reference | [公开来源](https://github.com/sansan0/TrendRadar) | `ir_search/infrastructure/rss.py`、`ir_search/adapters/rss_materials.py`；借鉴 RSS 素材发现、关键词过滤和去重；独立实现 RSS/Atom，不复制运行源码、不迁入调度/推送/AI。 |
| mutual-fund-data-expanded / mutual-fund-data: NAV / shares / holdings / listed fund quotes | adapted | 本地技能参考，不随包分发 | `ir_search/adapters/funds.py`、`ir_search/contracts/expanded_data.py`；单表只读、独立份额类别、原始/累计/复权净值、万份与百分比口径；实际表字段重新核对后在包内独立实现。 |
| etf-premium-reference / etf-premium | design_reference | 本地技能参考，不随包分发 | `ir_search/adapters/funds.py`；仅借鉴交易价和净值应分离、日期/币种对齐；不复制 Yahoo 代码或推断资金流。 |
| china-nav-tieout-reference / china-nav-tieout | design_reference | 本地技能参考，不随包分发 | `ir_search/adapters/funds.py`；仅借鉴净值、份额与持仓核对的输入约束；不采用未核实的分析规则。 |
| macro-regime-reference / macro-regime-detector | design_reference | 本地技能参考，不随包分发 | `ir_search/adapters/global_macro.py`；仅借鉴宏观序列需求，周期判断仍由调用 skill 完成，不引入 LLM 热路径。 |
| hkex-title-interface / HKEX official title search | design_reference | [公开来源](https://www.hkexnews.hk/search/titlesearch.xhtml) | `ir_search/infrastructure/official_materials.py`；核对官网标题搜索参数、股票目录与发行人原始披露链接；独立只读实现。 |
| fiona-formal-schema / Fiona official futures MCP guide | design_reference | [公开来源](https://www.finoview.com.cn/guide/mcp/futures_market_data.html) | `ir_search/adapters/fiona.py`；复用已验收 Bearer 客户端；按实际单对象/多行返回、合约场所和存续期校验，不猜风险指标单位。 |
| exa / Exa Search | service_client | [公开来源](https://exa.ai/docs/reference/search) | `ir_search/infrastructure/web_search.py`；独立网页发现客户端；不请求生成摘要 |
| bocha / 博查 Web Search | service_client | [公开来源](https://open.bochaai.com/) | `ir_search/infrastructure/web_search.py`；中文/国内网页发现客户端 |
| anysearch / AnySearch | service_client | [公开来源](https://anysearch.com/docs/api-endpoints) | `ir_search/infrastructure/web_search.py`；兼容网页发现客户端；与 Agent 原生搜索区分 |
| tavily / Tavily | reviewed_only | [公开来源](https://docs.tavily.com/documentation/api-reference/endpoint/search) | `docs/search_provider_comparison.md`；真实对照评估；新素材入口尚未接入 Tavily 搜索 |
| fmp / FMP Stable API | service_client | [公开来源](https://site.financialmodelingprep.com/developer/docs/stable) | `ir_search/adapters/fmp.py`；美股资料、基础行情和年度三表 |
| sec-edgar / SEC EDGAR | service_client | [公开来源](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | `ir_search/infrastructure/sec.py`；官方披露索引和原文 |
| fred / FRED | service_client | [公开来源](https://fred.stlouisfed.org/) | `ir_search/adapters/global_macro.py`；宏观序列 |
| ecb / ECB Data Portal | service_client | [公开来源](https://data.ecb.europa.eu/) | `ir_search/adapters/global_macro.py`；宏观汇率序列 |
| world-bank / World Bank Indicators API | service_client | [公开来源](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation) | `ir_search/adapters/global_macro.py`；宏观接口；本机真实 TLS 未通过 |

## 安装依赖与参考资料分开

依赖声明以 [pyproject.toml](../pyproject.toml) 为准：基础仅 PyYAML 及 Windows 时区数据；MCP、MySQL、AKShare、提取、Playwright、Crawl4AI、Scrapling、字幕和 WebSocket 音频分别属于 extras。小红书需独立后端，音频另需 FFmpeg，浏览器另需二进制。见[部署指南](standalone_deployment.md)。

本地参考快照、原文件、技能库存不进入交接包，也不是运行依赖。接手者可按公开 URL 与 revision 复核。无公开地址的条目只列技能名称，不分发私有路径、原文、凭证、真实响应或会话。

## rc2 测试依赖补充

开发 extra 增加 cryptography，用于在临时目录生成本机 TLS 取消测试的一次性证书；它不是搜索热路径或基础 SDK 的依赖，测试私钥不提交。HTTP/PyMySQL 缓冲测试使用标准库与已有可选 MySQL 驱动，不访问供应商。
