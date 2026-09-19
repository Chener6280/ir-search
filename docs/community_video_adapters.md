# 社区与视频素材 adapters

2026-09-17。本轮新增 `xueqiu`（雪球）、`eastmoney`（东方财富股吧）和 `video`（Bilibili、YouTube）。它们只进入 `search_materials` / `retrieve`，不改变国内行情与财务数据的 Wind/JYDB 分工，也不扩建 `deep_research`。音频后续已接入，见[小宇宙指南](xiaoyuzhou_audio_adapter.md)。

后续可用性改进：`YOUTUBE_DNS_MODE=google_doh` 可选择仅对固定 `www.youtube.com` 主机使用 [Google 官方 HTTPS DNS](https://developers.google.com/speed/public-dns/docs/doh/json)。默认 `system`；此选项不改变系统 DNS、代理或其他来源，仍校验公网 IP 与目标 TLS 证书。DNS 查询不带 Cookie/API Key，关闭 ECS，并在单次请求中按 TTL 有界复用。当前电脑已用此模式取得真实 YouTube 字幕；换电脑后仍需重新验证。它不能解决平台挑战、字幕权限或服务不可达。

`source_health` 披露选定 DNS 模式及雪球/Bilibili Cookie 是否配置，不返回值。2026-09-18 修复 HTTPS Host 头的兼容问题并刷新本机会话后，Bilibili 详情和字幕已通过 SDK/独立安装包 MCP 取数；雪球仍返回验证页，正文验收未通过。详见[会话与读取修复验收](platform_session_repair_acceptance.md)。配置存在不等于实时正文可用。

同日参照华福 skill 增加了雪球的**实验性浏览器读取后端**：`XUEQIU_READ_MODE=browser`，可选 `.[browser]` 依赖。默认保持 `http`。独立浏览器仍遇到验证循环，未宣布自动正文恢复；模式、依赖、预算、真实结果见[雪球浏览器修复记录](xueqiu_browser_repair.md)。

## 能力与边界

| provider | 搜索发现 | 正文或字幕 | 来源口径 |
|---|---|---|---|
| `xueqiu` | 博查，限雪球域名，筛选用户/帖子详情地址 | 主帖的文章选择器或页面内公开状态数据 | `social_post`、UGC、opinion；保留“来源”文字 |
| `eastmoney` | 博查，限 `guba.eastmoney.com`，筛选 `news,板块,帖子.html` | 页面内主帖数据：标题、正文、作者、发布时间 | `social_post`、UGC、opinion；资讯转载也不自动升为一手证据 |
| `video` / Bilibili | 博查，筛选 BV/av 视频详情链接 | 公开详情、指定分 P、平台可取得的字幕 | `video`；简介不是口述文稿 |
| `video` / YouTube | AnySearch，筛选 watch/shorts/live 视频详情链接 | oEmbed 元数据及可选字幕依赖返回的平台字幕 | `video`；人工/自动字幕和语言分别标记 |

社区与视频发现依赖搜索引擎的收录，不是站内完整检索。每个 adapter 最多检查 10 条搜索结果；视频候选数与正文读取预算由两个平台分摊，不按平台翻倍。主页、作者页、股吧列表、视频专栏文章、播放列表、站外转载和域名仿冒不会当作目标帖子或视频返回。尚不提供按作者穷尽全部历史帖、评论分页、完整频道更新流。

国内三个平台固定使用博查的域名 `include`；YouTube 固定使用 AnySearch 并作本地域名和视频 ID 校验。因此这里不受通用 `web_region` 切换影响。`WEB_ALLOWED_DOMAINS` 仍是上限，冲突的范围不会被放宽。`web_institutions` 和 `web_read_workers` 只适用于通用网页 adapter，不改变本轮平台的串行定额读取。

`published_start/end` 在本地按来源发布日期过滤；搜索引擎没有保证日期筛选或最新收录。未知日期保留并标记，不能用于“近七天全部内容”的完整性结论。雪球/Bilibili 时间戳和股吧发布时间采用中国时区。材料业务发生期仍然独立，不能用发帖日期替代。

## 配置与调用

每台电脑在私有凭证文件中分别启用：

```dotenv
XUEQIU_MATERIALS_ENABLED=true
EASTMONEY_MATERIALS_ENABLED=true
VIDEO_MATERIALS_ENABLED=true
XUEQIU_COOKIE=
BILIBILI_COOKIE=
```

复用 `BOCHA_API_KEY` / `ANYSEARCH_API_KEY`。两个 COOKIE 是可选的本机授权会话，默认空；只发送到对应的固定平台主机，不发送给搜索供应商、字幕 CDN 或跨域重定向。不会自动登录、取得验证码或绕过平台挑战。Cookie 不是官方 API Key，也不保证解决风控。已有密钥不写入示例、日志或返回结果。

账号、密码配置不等于已建立 HTTP 会话。这两个 adapter 使用本机显式配置的 Cookie；本次 Safari 导入只是本机维护操作，不作为运行时依赖随包发布。换电脑后需提供自己的有效会话。平台主帖读取遇到验证、认证或限流等停止条件后，本次请求停止该平台后续正文读取；已经发现的摘要保留原范围与 `prior_read_failure`，其他平台独立继续。

YouTube 字幕为可选依赖，核心安装不强制引入。在克隆的本仓库根目录安装：

```bash
python -m pip install '.[video,mcp]'
```

当前锁定并测试 `youtube-transcript-api==1.2.4`。这个库是第三方公共字幕客户端，并非 YouTube 官方授权接口。其网络请求经过本项目限额、超时、TLS 验证和公网地址校验，不继承浏览器登录、系统代理凭证或任意第三方网络请求。YouTube 官方 [captions.download](https://developers.google.com/youtube/v3/docs/captions/download) 需要适当的 OAuth 权限；普通 API Key 不等于可下载任意视频字幕。

```python
from ir_search import MaterialSearchRequest, MaterialRequest, RequestContext, search_materials, retrieve

# 首先预览来源与额度；不发起搜索、DNS 或正文请求。
preview = search_materials(MaterialSearchRequest(
    question="贵州茅台终端动销", keywords=["动销", "库存"],
    providers=["xueqiu", "eastmoney"],
    published_start="2026-09-01", published_end="2026-09-17",
    dry_run=True,
)).to_dict()

videos = search_materials(MaterialSearchRequest(
    question="NVIDIA earnings", keywords=["NVIDIA", "earnings"],
    providers=["video"], material_types=["video"],
    video_platforms=["youtube"], video_languages=["en", "zh-Hans"],
    published_start="2026-09-01", published_end="2026-09-17",
    candidates_per_source=8, text_reads_per_source=2,
), context=RequestContext(timeout_seconds=60, max_operations=30)).to_dict()

# 把搜索实际返回的 source_ref 传给 retrieve；也可用显式给定的公开详情链接。
if videos["items"]:
    source_ref = videos["items"][0]["versions"][0]["source_ref"]
    material = retrieve(MaterialRequest(
        question="NVIDIA earnings", urls=[source_ref], video_languages=["en"],
    ), context=RequestContext(timeout_seconds=60, max_operations=20)).to_dict()
```

MCP 使用同名工具和参数。`video_platforms` 支持 `bilibili`、`youtube`，默认两个；`video_languages` 为 1–8 个语言代码，默认 `zh-Hans, zh-CN, zh, en`，按偏好选择已有字幕，不自动翻译。

## 正文、字幕与引用

- 搜索摘要继续是 `search_snippet`，即使正文读取失败，也不改称原文。
- 社区主帖正文是 `extracted_text`；本轮不混入评论、排行榜、推荐视频或页面导航。
- 视频正文只来自实际取得的平台字幕，`read_details.content_origin=platform_captions`。`caption_kind` 区分 `manual`、`automatic` 和无法核实的 `unknown`；自动字幕有错误风险提示，不构成独立事实核验。
- 简介在 `read_details.description`，不会生成口述文本引用。只取得视频元数据时，`retrieve` 返回 `text_origin=metadata_only`、空文本、空证据列表及字幕缺口诊断。元数据和字幕都没取到则返回失败，不把输入 URL 包装成成功材料。
- 每段字幕有字符位置与 `start_ms/end_ms`。搜索证据直接附时间字段，`retrieve` 的证据在 `extra` 中附时间字段及 `timestamp_url`。时间范围对应与引文重叠的字幕段，不是逐词对齐；标题和搜索摘要没有时间引用。
- 字符引用继续绑定实际返回文本及 SHA-256。`all_returned_caption_rows_included` 仅说明此次字幕返回是否被字符预算截断，不保证视频每句话都有字幕。无语音识别、音视频下载、OCR、自动翻译或报告生成。
- Bilibili 明确返回需要登录时使用 `video_captions_login_required`。有字幕条目但没有下载地址时使用 `video_caption_url_unavailable`；如果调用方允许的其他语言条目有地址，可选择该条目并保留空地址警告。不会放宽域名、HTTPS 或重定向检查。
- Bilibili 校验字幕响应中的视频/分 P 标识，并报告 `caption_rows_returned`、`caption_returned_end_seconds` 和 `caption_completeness=not_verified`。字幕结束显著早于视频末尾时有 `video_caption_ends_before_video` 提示，这不等于证明存在漏字；字幕超出已知视频分段时长的容差则报 `video_caption_timing_mismatch`，仅返回不可引用的元数据。

失败诊断包括 `web_content_challenge`、`blocked_url`、`authentication_failed`、`video_dependency_missing`、`video_captions_unavailable`、`video_captions_login_required`、`video_caption_url_unavailable`、`video_caption_timing_mismatch`、`video_caption_language_unavailable`。源配置检查只确认本机配置，`live_verified=false` 不能被解释为已通过真实连通性验收。

## 参考与实测

雪球文章定位参考华福的本地 `xueqiu-scraper`，但保留原有 skill 会删除的来源行；运行时不导入或调用该 skill。字幕依赖参考 [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api)。Bilibili 与股吧公共页面/API属于非稳定的公开读取入口，不能视为官方长期 SLA；Bilibili [官方开放平台](https://open.bilibili.com/doc) 的创作者授权能力与本适配器的公共素材读取不同。

本地参考资源的维护方式见[参考资源库](reference_library.md)，实际成功项与受限项见[本轮验收记录](community_video_acceptance.md)。

后续音频扩展：小宇宙的独立搜索和 Agent Plan 转写已接入，见[音频指南](xiaoyuzhou_audio_adapter.md)。本页视频读取仍使用原有字幕链路，不自动转写视频。
